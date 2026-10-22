// Test-only input/output glue; calculations are extracted from pinned original C#.
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text.Json;
using Entities;
using DataMart;

internal static class ReferenceHarness
{
    static double N(JsonElement row, string key) => double.Parse(row.GetProperty(key).GetString(), CultureInfo.InvariantCulture);
    static string Q(double value) => value.ToString("R", CultureInfo.InvariantCulture);
    static DateTime D(string value) => DateTime.ParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture);
    static string Date(DateTime value) => value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
    static string Label(JsonElement value) => value.GetProperty("case").GetString();
    static HorizonCalendar Calendar(JsonElement root) => new()
    {
        Buckets = root.GetProperty("calendar").EnumerateArray().Select(row => new BucketSchema
        {
            BucketID = row.GetProperty("yyyyww").GetString(), BucketIndex = row.GetProperty("seq").GetInt32(),
            StartDate = D(row.GetProperty("start_date").GetString()), EndDate = D(row.GetProperty("end_date").GetString()),
            BaseMonth = new DateTime(2026, 9, 1)
        }).ToList()
    };
    static BufferPSIObject Buffer(HorizonCalendar calendar, double boh, double rop, double target,
                                  double lead, string policy, IEnumerable<double> demands)
    {
        var bpo = new BufferPSIObject
        {
            ItemID = "ITEM-A", BufferID = "TEST-ONLY", BOH = boh, AccPO = 0, AccBO = 0,
            StockLeadtime = lead, StockPolicy = policy,
            LotPolicy = new Lot { MinLotSize = 0, MaxLotSize = 1000000, IncreaseLotSize = 1 }
        };
        var so = demands.Select((quantity, index) => new SALES_ORDER
        { QTY = quantity, DUE_DTTM = calendar.Buckets[index].StartDate }).ToList();
        bpo.SetBucket(calendar, so, new List<SAFETY_STOCK>
        {
            new() { START_DTTM = new DateTime(2026, 1, 1), END_DTTM = new DateTime(2026, 12, 31),
                    ROP = rop, MAX_LV = target, MOS = 1, MA_DMD_QTY = 10 }
        }, new List<STOCK>());
        return bpo;
    }
    static object Replay(JsonElement item, JsonElement root, string policy)
    {
        var input = item.GetProperty("input");
        var bpo = Buffer(Calendar(root), N(input, "boh"), N(input, "rop"), N(input, "target"),
                         N(input, "legacy_internal_lead_time"), policy,
                         input.GetProperty("forecast").EnumerateArray().Select(v => double.Parse(v.GetString(), CultureInfo.InvariantCulture)));
        NativeKernel.Run(bpo);
        var ordered = bpo.PlanBuckets.Where(p => p.PurchaseOrder > 0).ToArray();
        return new { @case = Label(item), stock_policy = policy, actual = new
        {
            boh = bpo.PlanBuckets.Select(p => Q(p.BOH)).ToArray(),
            due_in = bpo.PlanBuckets.Select(p => Q(p.DueIn)).ToArray(),
            eoh = bpo.PlanBuckets.Select(p => Q(p.EOH)).ToArray(),
            backorder_close = bpo.PlanBuckets.Select(p => Q(p.AccBackOrder)).ToArray(),
            purchase_order = bpo.PlanBuckets.Select(p => Q(p.PurchaseOrder)).ToArray(),
            pending_po_close = bpo.PlanBuckets.Select(p => Q(p.AccPurchaseOrder)).ToArray(),
            demand_allocation = bpo.PlanBuckets.Select(p => Q(p.DemandAllocation)).ToArray(),
            stock_allocation = bpo.PlanBuckets.Select(p => Q(p.StockAllocation)).ToArray(),
            weekly_allocation_rate = bpo.PlanBuckets.Select(p => Q(p.DemandAllocationRate)).ToArray(),
            exp_mos = bpo.PlanBuckets.Select(p => Q(p.ExpMOS)).ToArray(),
            order_dates = ordered.Select(p => Date(p.Bucket.StartDate)).ToArray(),
            due_dates = ordered.Select(p => Date(p.Bucket.StartDate.AddDays(Definition.GetDaysfromLeadTIme(bpo.StockLeadtime)))).ToArray(),
            terminal_pending_po = Q(bpo.PlanBuckets.Last().AccPurchaseOrder)
        }};
    }
    static object Extended(JsonElement root, string inputPath)
    {
        var replays = root.GetProperty("replay_cases").EnumerateArray().Select(item =>
        {
            var input = item.GetProperty("input");
            var buckets = input.GetProperty("buckets").EnumerateArray().Select(row =>
            {
                var start = D(row.GetProperty("start").GetString());
                var b = new BucketSchema { BucketID = row.GetProperty("bucket_id").GetString(),
                    StartDate = start, EndDate = D(row.GetProperty("end").GetString()) };
                var month = row.GetProperty("base_month");
                NativeKernel.SetBaseMonth(b, new CALENDAR { START_DTTM = start,
                    BASE_MONTH = month.ValueKind == JsonValueKind.Null ? null : month.GetString() });
                return b;
            }).ToList();
            var lot = input.GetProperty("lot");
            var bpo = new BufferPSIObject { BOH = N(input, "boh"), AccBO = N(input, "backorder"),
                AccPO = N(input, "pending_po"), StockLeadtime = N(input, "internal_lead_time"),
                StockPolicy = input.GetProperty("stock_policy").GetString(),
                LotPolicy = new Lot { MinLotSize = N(lot, "minimum"), MaxLotSize = N(lot, "maximum"),
                    IncreaseLotSize = N(lot, "step") } };
            var demands = input.GetProperty("demands").EnumerateArray().Select(row =>
                new SALES_ORDER { DUE_DTTM = D(row.GetProperty("when").GetString()), QTY = N(row, "quantity") }).ToList();
            var policies = input.GetProperty("policies").EnumerateArray().Select(row =>
                new SAFETY_STOCK { START_DTTM = D(row.GetProperty("start").GetString()),
                    END_DTTM = D(row.GetProperty("end").GetString()), ROP = N(row, "rop"),
                    MAX_LV = N(row, "target"), MOS = N(row, "mos"), MA_DMD_QTY = N(row, "ma_demand") }).ToList();
            var receipts = input.GetProperty("receipts").EnumerateArray().Select(row =>
                new STOCK { USABLE_DTTM = D(row.GetProperty("when").GetString()), QTY = N(row, "quantity") }).ToList();
            bpo.SetBucket(new HorizonCalendar { Buckets = buckets }, demands, policies, receipts);
            NativeKernel.Run(bpo);
            return new { @case = Label(item), actual = new
            {
                bucket_ids = bpo.PlanBuckets.Select(p => p.Bucket.BucketID).ToArray(),
                demand = bpo.PlanBuckets.Select(p => Q(p.Demand)).ToArray(),
                boh = bpo.PlanBuckets.Select(p => Q(p.BOH)).ToArray(),
                due_in = bpo.PlanBuckets.Select(p => Q(p.DueIn)).ToArray(),
                eoh = bpo.PlanBuckets.Select(p => Q(p.EOH)).ToArray(),
                backorder = bpo.PlanBuckets.Select(p => Q(p.BackOrder)).ToArray(),
                backorder_close = bpo.PlanBuckets.Select(p => Q(p.AccBackOrder)).ToArray(),
                pending_po_close = bpo.PlanBuckets.Select(p => Q(p.AccPurchaseOrder)).ToArray(),
                purchase_order = bpo.PlanBuckets.Select(p => Q(p.PurchaseOrder)).ToArray(),
                demand_allocation = bpo.PlanBuckets.Select(p => Q(p.DemandAllocation)).ToArray(),
                stock_allocation = bpo.PlanBuckets.Select(p => Q(p.StockAllocation)).ToArray(),
                weekly_allocation_rate = bpo.PlanBuckets.Select(p => Q(p.DemandAllocationRate)).ToArray(),
                exp_mos = bpo.PlanBuckets.Select(p => Q(p.ExpMOS)).ToArray(),
                rop = bpo.PlanBuckets.Select(p => Q(p.ROP)).ToArray(),
                target = bpo.PlanBuckets.Select(p => Q(p.OUTL)).ToArray(),
                order_dates = bpo.PlanBuckets.Where(p => p.PurchaseOrder > 0).Select(p => Date(p.Bucket.StartDate)).ToArray(),
                due_dates = bpo.PlanBuckets.Where(p => p.PurchaseOrder > 0).Select(p => Date(p.Bucket.StartDate.AddDays(Definition.GetDaysfromLeadTIme(bpo.StockLeadtime)))).ToArray()
            }};
        }).ToArray();
        var rounds = root.GetProperty("round_cases").EnumerateArray().Select(item => new
            { @case = Label(item), actual = Q(Math.Round(N(item, "value"), item.GetProperty("digits").GetInt32())) }).ToArray();
        var ceilings = root.GetProperty("decimal_ceiling_cases").EnumerateArray().Select(item => new
            { @case = Label(item), actual = Q(Decimal.ToDouble(Math.Ceiling((decimal)N(item, "value")))) }).ToArray();
        var lots = root.GetProperty("lot_cases").EnumerateArray().Select(item => new
            { @case = Label(item), actual = Q(new Lot { MinLotSize = N(item, "min"), MaxLotSize = N(item, "max"),
                IncreaseLotSize = N(item, "step") }.GetSize(N(item, "raw"))) }).ToArray();
        return new { runtime = RuntimeInformation.FrameworkDescription,
            architecture = RuntimeInformation.ProcessArchitecture.ToString(),
            input_sha256 = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(inputPath))).ToLowerInvariant(),
            scope = "EXTRACTED_ORIGINAL_PSI_AND_DOTNET_NUMERIC_PRIMITIVES", replays, rounds, ceilings, lots };
    }
    public static void Main(string[] args)
    {
        CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
        using var doc = JsonDocument.Parse(File.ReadAllText(args[0]));
        var root = doc.RootElement;
        if (root.TryGetProperty("replay_cases", out _))
        {
            Console.WriteLine("NATIVE_REFERENCE_JSON:" + JsonSerializer.Serialize(Extended(root, args[0])));
            return;
        }
        var transitions = root.GetProperty("transition_cases").EnumerateArray().Select(item =>
        {
            var input = item.GetProperty("input");
            var row = new PlanBucket { Demand = N(input, "confirmed") + N(input, "forecast"), DueIn = N(input, "receipt") };
            NativeKernel.Advance(row, N(input, "boh"), N(input, "backorder"), N(input, "receipt"));
            return new { @case = Label(item), actual = new
            { eoh = Q(row.EOH), backorder_close = Q(row.AccBackOrder), demand_allocation = Q(row.DemandAllocation), stock_allocation = Q(row.StockAllocation), weekly_demand_allocation_rate = Q(row.DemandAllocationRate) }};
        }).ToArray();
        var orders = root.GetProperty("order_cases").EnumerateArray().SelectMany(item =>
            new[] { "OUTL", "ROPQ", "MINQ", "SFST" }.Select(policy => Replay(item, root, policy))).ToArray();
        var t = root.GetProperty("trigger_case");
        var tb = Buffer(Calendar(root), N(t, "position"), N(t, "rop"), N(t, "target"), 201600, "ROPQ", new double[] { 0, 0, 0 });
        NativeKernel.Run(tb);
        var trigger = new { @case = Label(t), actual = new { order_triggered = tb.PlanBuckets[0].PurchaseOrder > 0 } };
        var lots = root.GetProperty("lot_cases").EnumerateArray().Select(item => new
        {
            @case = Label(item), actual = Q(new Lot { MinLotSize = N(item, "min"), MaxLotSize = N(item, "max"), IncreaseLotSize = N(item, "step") }.GetSize(N(item, "raw")))
        }).ToArray();
        var arrivals = root.GetProperty("arrival_cases").EnumerateArray().Select(item =>
        {
            var calendar = Calendar(root);
            var bpo = Buffer(calendar, 0, 0, 0, N(item, "legacy_internal_lead_time"), "OUTL", new double[] { 0, 0, 0 });
            var days = Definition.GetDaysfromLeadTIme(bpo.StockLeadtime);
            var due = calendar.Buckets[0].StartDate.AddDays(days);
            bpo.SetDuein(due, 1);
            var bucket = bpo.PlanBuckets.SingleOrDefault(p => p.DueIn == 1);
            return new { @case = Label(item), actual = new { lead_time_days = days, due_date = Date(due), containing_bucket_start = bucket == null ? null : Date(bucket.Bucket.StartDate) }};
        }).ToArray();
        var policies = root.GetProperty("policy_cases").EnumerateArray().Select(item =>
        {
            var start = D(item.GetProperty("bucket_start").GetString());
            var month = item.GetProperty("base_month");
            var bucket = new BucketSchema { StartDate = start, EndDate = D(item.GetProperty("bucket_end").GetString()) };
            NativeKernel.SetBaseMonth(bucket, new CALENDAR { START_DTTM = start, BASE_MONTH = month.ValueKind == JsonValueKind.Null ? null : month.GetString() });
            var bpo = new BufferPSIObject();
            var sf = root.GetProperty("policy_schedule").EnumerateArray().Select(row => new SAFETY_STOCK
            { START_DTTM = D(row.GetProperty("effective_from").GetString()), END_DTTM = D(row.GetProperty("legacy_end_inclusive").GetString()), ROP = N(row, "rop") }).ToList();
            bpo.SetBucket(new HorizonCalendar { Buckets = new List<BucketSchema> { bucket } }, new List<SALES_ORDER>(), sf, new List<STOCK>());
            return new { @case = Label(item), actual = new { base_month = bucket.BaseMonth.ToString("yyyyMM"), rop = Q(bpo.PlanBuckets[0].ROP) }};
        }).ToArray();
        var selections = root.GetProperty("selection_cases").EnumerateArray().Where(item =>
            item.GetProperty("legacy_individual_condition_met").ValueKind != JsonValueKind.Null).Select(item =>
        {
            var scenarios = item.GetProperty("candidates").EnumerateArray().Select(row => new TB_PLN_SIMUL_SUM_SEG
            { SNRIO_ID = row.GetProperty("label").GetString(), SEGMT_ID = "TEST-GROUP", FILL_RATE = N(row, "fill_rate"), MOS = 1 }).ToList();
            new NativeSelector(N(item, "minimum_fill_rate")).ApplyFixedCondition(scenarios);
            return new { @case = Label(item), actual = new
            { individual_condition_met = scenarios.Select(s => s.INDV_FIXED_CONST01_FLAG == "Y").ToArray(), force_eligible_label = scenarios.FirstOrDefault(s => s.INDV_TARGET_FLAG_FORCE == "Y")?.SNRIO_ID, final_selected_label = (string)null }};
        }).ToArray();
        var report = new
        {
            runtime = RuntimeInformation.FrameworkDescription, architecture = RuntimeInformation.ProcessArchitecture.ToString(),
            input_sha256 = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(args[0]))).ToLowerInvariant(),
            scope = "EXTRACTED_ORIGINAL_METHODS_NO_DB_NO_ORIGINAL_ENTRYPOINT",
            additional_order_probe_inputs = new { min_lot = 0, max_lot = 1000000, multiple = 1, mos = 1, ma_dmd_qty = 10 },
            transitions, orders, trigger, lots, arrivals, policies, selections,
            unsupported = new[] { new { @case = "missing_cost_is_not_zero_cost", reason = "NO_COMPARABLE_LEGACY_COST_CONTRACT" } }
        };
        Console.WriteLine("NATIVE_REFERENCE_JSON:" + JsonSerializer.Serialize(report));
    }
}
