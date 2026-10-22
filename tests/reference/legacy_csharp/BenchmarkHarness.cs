// Benchmark-only input, timing and output glue. NativeKernel is unchanged original C#.
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Entities;
using DataMart;

internal static class BenchmarkHarness
{
    sealed record Work(HorizonCalendar Calendar, List<SALES_ORDER> Demands,
        List<STOCK> Receipts, List<SAFETY_STOCK> Policies, Lot Lot,
        double Boh, double Backorder, double Pending, double Lead, string Code);

    static DateTime D(JsonElement row, string name) => DateTime.ParseExact(
        row.GetProperty(name).GetString(), "yyyy-MM-dd", CultureInfo.InvariantCulture);
    static double N(JsonElement row, string name) => row.GetProperty(name).GetDouble();
    static double Seconds(long start, long end) => (end - start) / (double)Stopwatch.Frequency;

    static List<Work> Prepare(JsonElement root, int items)
    {
        if (items <= 0 || items > root.GetProperty("items").GetArrayLength())
            throw new ArgumentException("BENCHMARK_ITEM_COUNT");
        var calendar = new HorizonCalendar { Buckets = root.GetProperty("calendar").EnumerateArray()
            .Select(row => new BucketSchema { BucketID = row.GetProperty("bucket_id").GetString(),
                StartDate = D(row, "start"), EndDate = D(row, "end"),
                BaseMonth = new DateTime(D(row, "start").Year, D(row, "start").Month, 1) }).ToList() };
        var result = new List<Work>();
        foreach (var item in root.GetProperty("items").EnumerateArray().Take(items))
        {
            var demands = item.GetProperty("demands").EnumerateArray().Select(row =>
                new SALES_ORDER { DUE_DTTM = D(row, "when"), QTY = N(row, "quantity") }).ToList();
            var receipts = item.GetProperty("receipts").EnumerateArray().Select(row =>
                new STOCK { USABLE_DTTM = D(row, "when"), QTY = N(row, "quantity") }).ToList();
            var l = item.GetProperty("lot");
            var lot = new Lot { MinLotSize = N(l, "minimum"), MaxLotSize = N(l, "maximum"),
                IncreaseLotSize = N(l, "step") };
            foreach (var candidate in root.GetProperty("candidates").EnumerateArray())
            {
                var policy = new List<SAFETY_STOCK> { new() { START_DTTM = new DateTime(2026, 1, 1),
                    END_DTTM = new DateTime(2027, 12, 31), ROP = N(candidate, "rop"),
                    MAX_LV = N(candidate, "target"), MOS = N(candidate, "mos"),
                    MA_DMD_QTY = N(candidate, "ma_demand") } };
                result.Add(new Work(calendar, demands, receipts, policy, lot,
                    N(item, "boh"), N(item, "backorder"), N(item, "pending_po"),
                    N(item, "internal_lead_time"), candidate.GetProperty("stock_policy").GetString()));
            }
        }
        return result;
    }

    static List<BufferPSIObject> Compute(List<Work> requests)
    {
        var result = new List<BufferPSIObject>();
        foreach (var request in requests)
        {
            var bpo = new BufferPSIObject { BOH = request.Boh, AccBO = request.Backorder,
                AccPO = request.Pending, StockLeadtime = request.Lead,
                StockPolicy = request.Code, LotPolicy = request.Lot };
            bpo.SetBucket(request.Calendar, request.Demands, request.Policies, request.Receipts);
            NativeKernel.Run(bpo);
            result.Add(bpo);
        }
        return result;
    }

    static byte[] Serialize(List<BufferPSIObject> results, string inputHash, int items, int candidates, int weeks)
    {
        using var memory = new MemoryStream();
        using var writer = new BinaryWriter(memory, Encoding.UTF8, leaveOpen: true);
        writer.Write(Encoding.ASCII.GetBytes("BPSI0001"));
        writer.Write(Convert.FromHexString(inputHash));
        writer.Write(items * candidates * weeks);
        writer.Write(items); writer.Write(candidates); writer.Write(weeks);
        for (int index = 0; index < results.Count; index++)
        {
            var bpo = results[index];
            for (int week = 0; week < bpo.PlanBuckets.Length; week++)
            {
                var p = bpo.PlanBuckets[week];
                int order = p.PurchaseOrder > 0 ? (int)(p.Bucket.StartDate.Ticks / TimeSpan.TicksPerDay) + 1 : 0;
                int due = order == 0 ? 0 : order + Definition.GetDaysfromLeadTIme(bpo.StockLeadtime);
                writer.Write(index / candidates); writer.Write(index % candidates); writer.Write(week);
                writer.Write(order); writer.Write(due);
                foreach (double value in new double[] { p.Demand, p.DueIn, p.BOH, p.EOH, p.BackOrder,
                    p.AccBackOrder, p.AccPurchaseOrder, p.PurchaseOrder, p.DemandAllocation,
                    p.StockAllocation, p.DemandAllocationRate, p.ExpMOS, p.ROP, p.OUTL }) writer.Write(value);
            }
        }
        writer.Flush();
        return memory.ToArray();
    }

    static object Sample(List<Work> requests, string inputHash, int items, int candidates, int weeks,
        string path, string phase, int index)
    {
        long begin = Stopwatch.GetTimestamp();
        var results = Compute(requests);
        long computed = Stopwatch.GetTimestamp();
        var payload = Serialize(results, inputHash, items, candidates, weeks);
        long encoded = Stopwatch.GetTimestamp();
        var digest = Convert.ToHexString(SHA256.HashData(payload)).ToLowerInvariant();
        long hashed = Stopwatch.GetTimestamp();
        using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            file.Write(payload);
            file.Flush(flushToDisk: true);
        }
        long end = Stopwatch.GetTimestamp();
        return new { phase, index, compute_seconds = Seconds(begin, computed),
            serialize_seconds = Seconds(computed, encoded), hash_seconds = Seconds(encoded, hashed),
            store_seconds = Seconds(hashed, end), total_seconds = Seconds(begin, end),
            bytes = payload.Length, sha256 = digest, file = Path.GetFileName(path), rows = items * candidates * weeks };
    }

    public static void Main(string[] args)
    {
#if DEBUG
        throw new InvalidOperationException("RELEASE_BUILD_REQUIRED");
#else
        CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
        long started = Stopwatch.GetTimestamp();
        var raw = File.ReadAllBytes(args[0]);
        var hash = Convert.ToHexString(SHA256.HashData(raw)).ToLowerInvariant();
        using var document = JsonDocument.Parse(raw);
        var root = document.RootElement;
        int items = int.Parse(args[2], CultureInfo.InvariantCulture);
        var requests = Prepare(root, items);
        long prepared = Stopwatch.GetTimestamp();
        if (Directory.Exists(args[1])) throw new IOException("FRESH_RESULT_DIRECTORY_REQUIRED");
        Directory.CreateDirectory(args[1]);
        int candidates = root.GetProperty("candidates").GetArrayLength();
        int weeks = root.GetProperty("calendar").GetArrayLength();
        var execution = root.GetProperty("protocol").GetProperty("execution");
        var samples = new List<object>();
        var phases = args.Contains("--verify-only") ? new[] { ("verification", "verification_calls") }
            : new[] { ("cold", "cold_calls"), ("warmup", "warmup_calls"), ("measured", "measured_calls") };
        foreach (var entry in phases)
            for (int index = 0; index < execution.GetProperty(entry.Item2).GetInt32(); index++)
                samples.Add(Sample(requests, hash, items, candidates, weeks,
                    Path.Combine(args[1], $"{entry.Item1}-{index}.bin"), entry.Item1, index));
        using var process = Process.GetCurrentProcess();
        Console.WriteLine(JsonSerializer.Serialize(new { language = "csharp", runtime = RuntimeInformation.FrameworkDescription,
            architecture = RuntimeInformation.ProcessArchitecture.ToString(), items, candidates, weeks, input_sha256 = hash,
            preparation_seconds = Seconds(started, prepared), peak_rss_bytes = process.PeakWorkingSet64, samples }));
#endif
    }
}
