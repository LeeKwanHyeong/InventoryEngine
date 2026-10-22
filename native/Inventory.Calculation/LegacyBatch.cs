using System.Text.Json;
using DataMart;
using Entities;

namespace Inventory.Calculation;

// Generalizes input/output only. NativeKernel, Lot and SetBucket stay pinned.
public static partial class Kernel
{
    public static byte[] RunLegacy(byte[] source, CancellationToken cancellation)
    {
        using var doc = Wire.Parse(source);
        var root = doc.RootElement;
        Wire.Fields(root, "binding", "calendar", "requests");
        Wire.Need(root.GetProperty("binding").GetProperty("semantics_profile").GetString()
            == "LEGACY_REPLAY", "DLL_PROFILE_MISMATCH");
        var calendar = new HorizonCalendar();
        foreach (var row in root.GetProperty("calendar").EnumerateArray())
        {
            Wire.Fields(row, "yyyyww", "seq", "start_date", "end_date", "base_month");
            calendar.Buckets.Add(new BucketSchema {
                BucketID = row.GetProperty("yyyyww").GetString(),
                BucketIndex = row.GetProperty("seq").GetInt32(),
                StartDate = Wire.Date(row, "start_date"), EndDate = Wire.Date(row, "end_date"),
                BaseMonth = DateTime.ParseExact(row.GetProperty("base_month").GetString(),
                    "yyyyMM", System.Globalization.CultureInfo.InvariantCulture) });
        }
        int count = root.GetProperty("requests").GetArrayLength();
        Wire.Need(calendar.Buckets.Count is > 0 and <= 520 && count > 0
            && (long)count * calendar.Buckets.Count <= Wire.MaxRows, "DLL_GRID_LIMIT");
        var results = new List<object>();
        foreach (var row in root.GetProperty("requests").EnumerateArray())
        {
            cancellation.ThrowIfCancellationRequested();
            Wire.Fields(row, "item_id", "candidate_id", "boh", "backorder", "pending_po",
                "internal_lead_time", "stock_policy", "lot", "policies", "demands", "receipts");
            var lot = row.GetProperty("lot");
            Wire.Fields(lot, "minimum", "maximum", "step");
            double minimum = Wire.Number(lot, "minimum"), maximum = Wire.Number(lot, "maximum"),
                step = Wire.Number(lot, "step");
            Wire.Need(step > 0 && minimum <= maximum, "DLL_LOT_RANGE");
            double lead = Wire.Number(row, "internal_lead_time");
            Wire.Need(lead / 28800 <= int.MaxValue, "DLL_LEAD_RANGE");
            var bpo = new BufferPSIObject {
                ItemID = row.GetProperty("item_id").GetString(),
                BOH = Wire.Number(row, "boh"), AccBO = Wire.Number(row, "backorder"),
                AccPO = Wire.Number(row, "pending_po"), StockLeadtime = lead,
                StockPolicy = row.GetProperty("stock_policy").GetString(),
                LotPolicy = new Lot { MinLotSize = minimum, MaxLotSize = maximum,
                    IncreaseLotSize = step } };
            var demand = row.GetProperty("demands").EnumerateArray().Select(x => {
                Wire.Fields(x, "when", "quantity");
                return new SALES_ORDER { DUE_DTTM = Wire.Date(x, "when"), QTY = Wire.Number(x, "quantity") };
            }).ToList();
            var receipts = row.GetProperty("receipts").EnumerateArray().Select(x => {
                Wire.Fields(x, "when", "quantity");
                return new STOCK { USABLE_DTTM = Wire.Date(x, "when"), QTY = Wire.Number(x, "quantity") };
            }).ToList();
            var policies = row.GetProperty("policies").EnumerateArray().Select(x => {
                Wire.Fields(x, "start", "end", "rop", "target", "mos", "ma_demand");
                return new SAFETY_STOCK { START_DTTM = Wire.Date(x, "start"), END_DTTM = Wire.Date(x, "end"),
                    ROP = Wire.Number(x, "rop"), MAX_LV = Wire.Number(x, "target"),
                    MOS = Wire.Number(x, "mos"), MA_DMD_QTY = Wire.Number(x, "ma_demand") };
            }).ToList();
            bpo.SetBucket(calendar, demand, policies, receipts);
            NativeKernel.Run(bpo);
            cancellation.ThrowIfCancellationRequested();
            foreach (var p in bpo.PlanBuckets)
            {
                cancellation.ThrowIfCancellationRequested();
                var quantities = new Dictionary<string, string> {
                    ["demand"] = Wire.Number(p.Demand), ["due_in"] = Wire.Number(p.DueIn),
                    ["boh"] = Wire.Number(p.BOH), ["eoh"] = Wire.Number(p.EOH),
                    ["backorder"] = Wire.Number(p.BackOrder), ["backorder_close"] = Wire.Number(p.AccBackOrder),
                    ["pending_po_close"] = Wire.Number(p.AccPurchaseOrder), ["purchase_order"] = Wire.Number(p.PurchaseOrder),
                    ["demand_allocation"] = Wire.Number(p.DemandAllocation), ["stock_allocation"] = Wire.Number(p.StockAllocation),
                    ["weekly_allocation_rate"] = Wire.Number(p.DemandAllocationRate), ["exp_mos"] = Wire.Number(p.ExpMOS),
                    ["rop"] = Wire.Number(p.ROP), ["target"] = Wire.Number(p.OUTL) };
                string order = p.PurchaseOrder > 0 ? p.Bucket.StartDate.ToString("yyyy-MM-dd") : null;
                string due = order == null ? null : p.Bucket.StartDate.AddDays(
                    Definition.GetDaysfromLeadTIme(lead)).ToString("yyyy-MM-dd");
                results.Add(new { item_id = bpo.ItemID, candidate_id = row.GetProperty("candidate_id").GetString(),
                    yyyyww = p.Bucket.BucketID, quantities, order_date = order, due_date = due });
            }
        }
        return Wire.Result(new { binding = root.GetProperty("binding"), rows = results });
    }
}
