using System.Globalization;
using System.Text.Json;

namespace Inventory.Calculation;

internal static class Wire
{
    internal const int MaxBytes = 8_000_000;
    internal const int MaxRows = 200_000;
    internal static void Need(bool ok, string code)
    {
        if (!ok) throw new ArgumentException(code);
    }
    internal static JsonDocument Parse(byte[] bytes)
    {
        Need(bytes != null && bytes.Length > 0 && bytes.Length <= MaxBytes, "DLL_INPUT_LIMIT");
        var doc = JsonDocument.Parse(bytes);
        try { Unique(doc.RootElement); return doc; }
        catch { doc.Dispose(); throw; }
    }
    private static void Unique(JsonElement row)
    {
        if (row.ValueKind == JsonValueKind.Object)
        {
            var names = new HashSet<string>();
            foreach (var p in row.EnumerateObject())
            { Need(names.Add(p.Name), "DLL_DUPLICATE_FIELD"); Unique(p.Value); }
        }
        else if (row.ValueKind == JsonValueKind.Array)
            foreach (var item in row.EnumerateArray()) Unique(item);
    }
    internal static void Fields(JsonElement row, params string[] names)
    {
        Need(row.ValueKind == JsonValueKind.Object, "DLL_OBJECT_REQUIRED");
        Need(row.EnumerateObject().Select(x => x.Name).ToHashSet().SetEquals(names), "DLL_FIELDS");
    }
    internal static DateTime Date(JsonElement row, string field) => DateTime.ParseExact(
        row.GetProperty(field).GetString(), "yyyy-MM-dd", CultureInfo.InvariantCulture);
    internal static double Number(JsonElement row, string field)
    {
        var n = double.Parse(row.GetProperty(field).GetString(), CultureInfo.InvariantCulture);
        Need(double.IsFinite(n) && n >= 0, "DLL_FINITE_NONNEGATIVE_REQUIRED");
        return n;
    }
    internal static string Number(double n)
    {
        Need(double.IsFinite(n), "DLL_NONFINITE_RESULT");
        return n.ToString("R", CultureInfo.InvariantCulture);
    }
    internal static byte[] Result(object value)
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(value);
        Need(bytes.Length <= 128_000_000, "DLL_OUTPUT_LIMIT");
        return bytes;
    }
}
