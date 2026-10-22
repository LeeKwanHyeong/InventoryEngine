# Local experiment only. Product environments and pyproject dependencies are unchanged.
FROM inventory-legacy-psi-benchmark:20261021
COPY PythonNet.requirements.txt /experiment-requirements.txt
RUN python3 -m pip install --no-cache-dir --only-binary=:all: -r /experiment-requirements.txt \
    && python3 -m pip check
