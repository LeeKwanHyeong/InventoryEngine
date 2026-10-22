# Inventory.Calculation

Opt-in, DB-free .NET 8 library. See [the Canonical Adapter report](../../docs/architecture/IO_CANONICAL_DLL_ADAPTER.md).
`RunLegacy` wraps pinned original fragments without modifying their arithmetic.
`AdvanceTarget` is a separate exact numerical transition, not a strategy or Guard.
Neither is registered in a Runtime or grants operational admission/publication.

Prepare a **fresh external directory** using the existing fingerprint-checked extractor:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src python scripts/prepare_calculation_dll.py \
  --source-root '/path/to/DSIO_postgresql_20250908/RootIO' \
  --destination /private/tmp/io-calculation-UNIQUE/stage
```

Build Release offline using the local .NET 8 SDK. With the stage mounted read-only at `/source`
and a fresh build-output directory at `/build`:

```sh
dotnet build /source/Inventory.Calculation.csproj --configfile /source/NuGet.Config \
  -c Release -p:LegacySources=/source -p:BaseIntermediateOutputPath=/tmp/obj/ \
  -p:MSBuildProjectExtensionsPath=/tmp/obj/ -o /build
```

No NuGet sources/packages are needed. Use a separate process for each assembly Build identity.
Load the existing pinned Python.NET development image, mount the repo and build read-only,
disable network and run with `PYTHONPATH=/repo:/repo/src:/repo/tests/support`:

```sh
python3 /repo/scripts/verify_calculation_dll.py --build /build
```

The verifier derives a local Build hash for diagnostic testing only. A future Runtime must
use its trusted installed Build binding instead of accepting a caller-supplied DLL/hash pair.
Hard timeout requires stopping the isolated worker. Cooperative cancellation is checked at
Legacy candidate and serialization boundaries; the original weekly loop is not modified.
The target primitive checks cancellation before/after the transition. No checkpoint/restart
or publication recovery is implemented by this library.
