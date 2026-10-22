// Experimental, DB-free facade. NativeKernel and its DTOs are pinned original fragments.
using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using DataMart;
using Entities;

namespace Inventory.Legacy;

public static class PsiKernel
{
    public static PreparedBatch Prepare(byte[] source, int items)
    {
        using var document = JsonDocument.Parse(source);
        return new PreparedBatch(document.RootElement, items, SHA256.HashData(source));
    }
}

public sealed class BatchResult
{
    public byte[] Payload { get; internal init; }
    public double ComputeSeconds { get; internal init; }
    public double SerializeSeconds { get; internal init; }
}

public sealed class PreparedBatch
{
    private sealed record Work(HorizonCalendar Calendar, List<SALES_ORDER> Demand,
        List<STOCK> Receipts, List<SAFETY_STOCK> Policy, Lot Lot,
        double Boh, double Backorder, double Pending, double Lead, string Code);
    private readonly Work[] requests;
    private readonly byte[] sourceHash;
    public int Items { get; }
    public int Candidates { get; }
    public int Weeks { get; }
    public int RowCount => checked(Items * Candidates * Weeks);
    private static double N(JsonElement row, string name) => row.GetProperty(name).GetDouble();
    private static DateTime D(JsonElement row, string name) => DateTime.ParseExact(
        row.GetProperty(name).GetString(), "yyyy-MM-dd", CultureInfo.InvariantCulture);

    internal PreparedBatch(JsonElement root, int items, byte[] hash)
    {
        // A deliberately bounded benchmark contract, not an operational Canonical adapter.
        if (items < 1 || items > 16 || items > root.GetProperty("items").GetArrayLength())
            throw new ArgumentException("PYTHONNET_ITEM_COUNT");
        Items = items;
        Candidates = root.GetProperty("candidates").GetArrayLength();
        Weeks = root.GetProperty("calendar").GetArrayLength();
        if (Candidates != 32 || Weeks != 52) throw new ArgumentException("PYTHONNET_GRAIN");
        sourceHash = hash;
        var calendar = new HorizonCalendar { Buckets = root.GetProperty("calendar").EnumerateArray()
            .Select(row => new BucketSchema { BucketID = row.GetProperty("bucket_id").GetString(),
                StartDate = D(row, "start"), EndDate = D(row, "end"),
                BaseMonth = new DateTime(D(row, "start").Year, D(row, "start").Month, 1) }).ToList() };
        var list = new List<Work>();
        foreach (var item in root.GetProperty("items").EnumerateArray().Take(items))
        {
            var demand = item.GetProperty("demands").EnumerateArray().Select(row =>
                new SALES_ORDER { DUE_DTTM = D(row, "when"), QTY = N(row, "quantity") }).ToList();
            var receipts = item.GetProperty("receipts").EnumerateArray().Select(row =>
                new STOCK { USABLE_DTTM = D(row, "when"), QTY = N(row, "quantity") }).ToList();
            var lot = item.GetProperty("lot");
            foreach (var candidate in root.GetProperty("candidates").EnumerateArray())
                list.Add(new Work(calendar, demand, receipts, new List<SAFETY_STOCK> { new() {
                    START_DTTM = new DateTime(2026, 1, 1), END_DTTM = new DateTime(2027, 12, 31),
                    ROP = N(candidate, "rop"), MAX_LV = N(candidate, "target"),
                    MOS = N(candidate, "mos"), MA_DMD_QTY = N(candidate, "ma_demand") } },
                    new Lot { MinLotSize = N(lot, "minimum"), MaxLotSize = N(lot, "maximum"),
                        IncreaseLotSize = N(lot, "step") },
                    N(item, "boh"), N(item, "backorder"), N(item, "pending_po"),
                    N(item, "internal_lead_time"), candidate.GetProperty("stock_policy").GetString()));
        }
        requests = list.ToArray();
    }

    public BatchResult Run(CancellationToken cancellation)
    {
        long start = Stopwatch.GetTimestamp();
        var results = new List<BufferPSIObject>(requests.Length);
        foreach (var request in requests)
        {
            // Cooperative cancellation at item/candidate boundaries; original weekly loop is unchanged.
            cancellation.ThrowIfCancellationRequested();
            var bpo = new BufferPSIObject { BOH = request.Boh, AccBO = request.Backorder,
                AccPO = request.Pending, StockLeadtime = request.Lead, StockPolicy = request.Code,
                LotPolicy = request.Lot };
            bpo.SetBucket(request.Calendar, request.Demand, request.Policy, request.Receipts);
            NativeKernel.Run(bpo);
            results.Add(bpo);
        }
        cancellation.ThrowIfCancellationRequested();
        long computed = Stopwatch.GetTimestamp();
        using var memory = new MemoryStream(checked(56 + 132 * RowCount));
        using var writer = new BinaryWriter(memory, Encoding.UTF8, leaveOpen: true);
        writer.Write(Encoding.ASCII.GetBytes("BPSI0001"));
        writer.Write(sourceHash);
        writer.Write(RowCount); writer.Write(Items); writer.Write(Candidates); writer.Write(Weeks);
        for (int index = 0; index < results.Count; index++)
        {
            cancellation.ThrowIfCancellationRequested();
            var bpo = results[index];
            for (int week = 0; week < bpo.PlanBuckets.Length; week++)
            {
                var p = bpo.PlanBuckets[week];
                int order = p.PurchaseOrder > 0 ? (int)(p.Bucket.StartDate.Ticks / TimeSpan.TicksPerDay) + 1 : 0;
                writer.Write(index / Candidates); writer.Write(index % Candidates); writer.Write(week);
                writer.Write(order);
                writer.Write(order == 0 ? 0 : order + Definition.GetDaysfromLeadTIme(bpo.StockLeadtime));
                foreach (double value in new double[] { p.Demand, p.DueIn, p.BOH, p.EOH,
                    p.BackOrder, p.AccBackOrder, p.AccPurchaseOrder, p.PurchaseOrder,
                    p.DemandAllocation, p.StockAllocation, p.DemandAllocationRate, p.ExpMOS, p.ROP, p.OUTL })
                    writer.Write(value);
            }
        }
        writer.Flush();
        var payload = memory.ToArray();
        cancellation.ThrowIfCancellationRequested();
        return new BatchResult { Payload = payload,
            ComputeSeconds = (computed - start) / (double)Stopwatch.Frequency,
            SerializeSeconds = (Stopwatch.GetTimestamp() - computed) / (double)Stopwatch.Frequency };
    }
}
