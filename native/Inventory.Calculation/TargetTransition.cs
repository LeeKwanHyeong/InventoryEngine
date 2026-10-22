using System.Numerics;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Inventory.Calculation;

// Separate exact target transition; not a translated Legacy loop or a strategy/Guard.
public static partial class Kernel
{
    private static readonly BigInteger Scale = BigInteger.Pow(10, 6);
    private static BigInteger Q(JsonElement row, string key)
    {
        string text = row.GetProperty(key).GetString();
        Wire.Need(text != null && text.Length <= 48
            && Regex.IsMatch(text, @"\A(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?\z"), "DLL_TARGET_PRECISION");
        var parts = text.Split('.');
        var n = BigInteger.Parse(parts[0]) * Scale
            + (parts.Length == 1 ? 0 : BigInteger.Parse(parts[1].PadRight(6, '0')));
        Check(n); return n;
    }
    private static void Check(BigInteger n)
    {
        // Fixed six-place arithmetic; all intermediates fit Python's 40-digit context exactly.
        Wire.Need(n >= 0 && n.ToString().Length <= 40, "DLL_TARGET_PRECISION");
    }
    private static string T(BigInteger n)
    {
        Check(n);
        var integer = BigInteger.DivRem(n, Scale, out var fraction);
        return fraction.IsZero ? integer.ToString() : integer + "." + fraction.ToString().PadLeft(6, '0').TrimEnd('0');
    }
    public static byte[] AdvanceTarget(byte[] source, CancellationToken cancellation)
    {
        using var doc = Wire.Parse(source);
        var r = doc.RootElement;
        Wire.Fields(r, "available_qty", "reserved_qty", "backorder_qty", "confirmed_customer_order_qty",
            "net_forecast_qty", "confirmed_supplier_receipt_qty", "recommended_receipt_qty");
        cancellation.ThrowIfCancellationRequested();
        var boh = Q(r, "available_qty"); var reserved = Q(r, "reserved_qty");
        var backlog = Q(r, "backorder_qty"); var confirmed = Q(r, "confirmed_customer_order_qty");
        var forecast = Q(r, "net_forecast_qty"); var inbound = Q(r, "confirmed_supplier_receipt_qty");
        var recommended = Q(r, "recommended_receipt_qty");
        var available = boh + inbound + recommended; Check(available); Check(backlog + confirmed);
        var fulfilledBo = BigInteger.Min(available, backlog);
        var fulfilledOrder = BigInteger.Min(available - fulfilledBo, confirmed);
        var fulfilledForecast = BigInteger.Min(available - fulfilledBo - fulfilledOrder, forecast);
        var eoh = available - fulfilledBo - fulfilledOrder - fulfilledForecast;
        var closeBo = backlog + confirmed - fulfilledBo - fulfilledOrder;
        var result = new Dictionary<string, string> {
            ["boh_qty"] = T(boh), ["reserved_qty"] = T(reserved), ["on_hand_boh_qty"] = T(boh + reserved),
            ["confirmed_supplier_receipt_qty"] = T(inbound), ["recommended_receipt_qty"] = T(recommended),
            ["available_qty"] = T(available), ["backorder_open_qty"] = T(backlog),
            ["fulfilled_backorder_qty"] = T(fulfilledBo), ["fulfilled_confirmed_customer_order_qty"] = T(fulfilledOrder),
            ["fulfilled_forecast_qty"] = T(fulfilledForecast), ["eoh_qty"] = T(eoh),
            ["on_hand_eoh_qty"] = T(eoh + reserved), ["backorder_close_qty"] = T(closeBo),
            ["forecast_shortage_qty"] = T(forecast - fulfilledForecast) };
        cancellation.ThrowIfCancellationRequested();
        return Wire.Result(result);
    }
}
