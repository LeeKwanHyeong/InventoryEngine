FROM python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS python
FROM mcr.microsoft.com/dotnet/sdk@sha256:f7e28a0a3f6f9235929ec19274fd0ddf8534e9159f99fac602ac73304f1ce99f
COPY --from=python /usr/local /usr/local
ENV LD_LIBRARY_PATH=/usr/local/lib PYTHONDONTWRITEBYTECODE=1
RUN python3 --version && python3 -c "import json,hashlib,datetime,resource,struct" && dotnet --version
