import importlib.util
import json
import re
from pathlib import Path

from prometheus_client import REGISTRY

import fritzlogextractor.metrics  # noqa: F401  (registers the metrics)

SCRIPT = Path(__file__).parent.parent / "examples" / "grafana" / "build_dashboard.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_dashboard", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_json_is_up_to_date():
    builder = load_builder()

    assert builder.OUTPUT.read_text() == builder.render(), (
        "Run: uv run python examples/grafana/build_dashboard.py"
    )


def test_dashboard_uses_every_metric_of_the_tool():
    text = load_builder().OUTPUT.read_text()
    metrics = {
        metric.name
        for metric in REGISTRY.collect()
        if metric.name.startswith("fritzlogextractor_")
    }
    # Counters are queried with their _total suffix, info metrics with _info.
    used = set(re.findall(r"fritzlogextractor_[a-z_]+", text))

    missing = {m for m in metrics if not {m, f"{m}_total", f"{m}_info"} & used}
    assert not missing


def test_panels_do_not_overlap_and_ids_are_unique():
    panels = json.loads(load_builder().OUTPUT.read_text())["panels"]
    cells = set()
    for panel in panels:
        pos = panel["gridPos"]
        assert pos["x"] + pos["w"] <= 24
        for x in range(pos["x"], pos["x"] + pos["w"]):
            for y in range(pos["y"], pos["y"] + pos["h"]):
                assert (x, y) not in cells, panel["title"]
                cells.add((x, y))
    assert len({p["id"] for p in panels}) == len(panels)
