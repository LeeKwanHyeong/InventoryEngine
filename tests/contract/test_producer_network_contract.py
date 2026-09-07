"""Optional cross-repository parity test. Runtime code has no platform dependency."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.network import (
    LANE_KEYS,
    NODE_KEYS,
    NUMERIC_LANE,
    normalize_network,
    sha256,
)


@unittest.skipUnless(
    os.environ.get("DSAI_PLATFORM_ROOT"), "producer source not explicitly provided"
)
class ProducerParityTests(unittest.TestCase):
    def test_all_ten_producer_seed_hashes_match_independent_consumer(self):
        root = Path(os.environ["DSAI_PLATFORM_ROOT"])
        path = root / "data_layer/repositories/inventory_network/contract.py"
        spec = importlib.util.spec_from_file_location("network_producer_contract", path)
        producer = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = producer
        spec.loader.exec_module(producer)
        self.assertEqual(NODE_KEYS, producer.NODE_KEYS)
        self.assertEqual(LANE_KEYS, producer.LANE_KEYS)
        self.assertEqual(NUMERIC_LANE, producer.NUMERIC_LANE)
        data = root.parent / "InventoryEngine/docs/architecture/data"
        bundles = producer.load_seed(
            data / "tb_mst_inventory_network_node_seed.csv",
            data / "tb_mst_inventory_network_lane_seed.csv",
            company_cd="DSE",
        )
        self.assertEqual(len(bundles), 10)
        for bundle in bundles:
            with self.subTest(revision=bundle.revision_id):
                self.assertEqual(sha256(normalize_network(bundle.data)), bundle.sha256)


if __name__ == "__main__":
    unittest.main()
