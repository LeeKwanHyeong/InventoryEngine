// Direct facade baseline: same DLL, input, serialization, hash and file flush as Python.NET.
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using Inventory.Legacy;

byte[] input = File.ReadAllBytes(args[0]);
string output = args[1];
int items = int.Parse(args[2]);
Directory.CreateDirectory(output);
var samples = new List<object>();
for (int index = 0; index < 11; index++)
{
    string phase = index == 0 ? "cold" : index <= 3 ? "warmup" : "measured";
    long begin = Stopwatch.GetTimestamp();
    var batch = PsiKernel.Prepare(input, items);
    long prepared = Stopwatch.GetTimestamp();
    var result = batch.Run(CancellationToken.None);
    long called = Stopwatch.GetTimestamp();
    string hash = Convert.ToHexString(SHA256.HashData(result.Payload)).ToLowerInvariant();
    long verified = Stopwatch.GetTimestamp();
    string name = $"{index:D2}.bin";
    using (var file = new FileStream(Path.Combine(output, name), FileMode.CreateNew, FileAccess.Write))
    { file.Write(result.Payload); file.Flush(flushToDisk: true); }
    long end = Stopwatch.GetTimestamp();
    double S(long a, long b) => (b - a) / (double)Stopwatch.Frequency;
    samples.Add(new { phase, index, input_copy_seconds = 0.0, prepare_seconds = S(begin, prepared),
        compute_seconds = result.ComputeSeconds, serialize_seconds = result.SerializeSeconds,
        managed_call_seconds = S(prepared, called), output_copy_seconds = 0.0,
        verify_seconds = S(called, verified), store_seconds = S(verified, end),
        total_seconds = S(begin, verified), with_store_seconds = S(begin, end),
        file = name, bytes = result.Payload.Length, rows = batch.RowCount, sha256 = hash });
}
Console.WriteLine(JsonSerializer.Serialize(new { mode = "csharp_direct", items,
    input_sha256 = Convert.ToHexString(SHA256.HashData(input)).ToLowerInvariant(),
    runtime = RuntimeInformation.FrameworkDescription, peak_rss_bytes = Process.GetCurrentProcess().PeakWorkingSet64,
    samples }));
