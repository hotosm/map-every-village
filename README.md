# Map Every Village

Finds African villages that are likely missing from OpenStreetMap, using AI
building predictions. The results are ready to verify in the field with
[Field-TM](https://github.com/hotosm/field-tm).

```bash
just setup          # builds the image; needs only Docker and just
just db-up
just run SLE        # one or more ISO3 codes; results in out/SLE/
```

- [How it works](docs/how-it-works.md): the pipeline, clustering defaults and
  outputs.
- [Running it](docs/running.md): setup, batch runs and reruns.
- [Tuning](docs/tuning.md): when to change clustering settings and what we tested.
- [Prioritising](docs/prioritising.md): size classes, field priority, and how
  our counts compare with official village counts.

## Data

All inputs are openly licensed:

- Buildings: VIDA Google-Microsoft-OSM Open Buildings (ODbL)
- OSM: HOT's exports via [oex](https://github.com/osgeonepal/oex) (ODbL)
- Boundaries: OCHA COD-AB (CC BY-IGO), else geoBoundaries (CC BY 4.0), else OSM (ODbL)
- Name hints: GeoNames (CC BY 4.0)

Outputs are ODbL. Code is GPL-3.0.
