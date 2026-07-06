import json
import requests
from pathlib import Path
from duck_soup.config import load_config

cfg = load_config(Path("pipelines/test.yaml"))
src = next(s for s in cfg.pipelines[0].sources if s.id == "places")

fake_cfg_dict = {
    "name": "preview",
    "output": "preview.gpkg",
    "pipelines": [{
        "name": "preview",
        "sources": [src.model_dump(by_alias=True, exclude_none=True)],
        "base": "places",
        "steps": [],
        "mapping": [],
        "layer": "preview",
        "crs": src.crs or "EPSG:4326"
    }]
}

payload = {
    "config": fake_cfg_dict,
    "pipeline_idx": 0,
    "limit": 50
}

r = requests.post("http://localhost:8000/api/preview", json=payload)
print(r.status_code)
print(json.dumps(r.json(), indent=2))
