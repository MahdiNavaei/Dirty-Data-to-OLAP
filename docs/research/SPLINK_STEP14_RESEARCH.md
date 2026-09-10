# Splink Step14 Research Record

- Runtime dependency selected: `splink==4.0.17`.
- Stable package metadata was inspected from the official PyPI release and
  official source tag. The package declares Python `>=3.10,<4` and MIT
  licensing.
- Research-only pinned commit inspected: `ca89ee92d5472b5e5de71cff3001193e04faf0e7`.
  It was used to compare the development API and was not copied into project
  source.
- The stable runtime exposes the required official machinery: `Linker`,
  `SettingsCreator`, blocking rules, random-u estimation, EM m-training,
  prediction and threshold clustering.
- The project deliberately selects the stable 4.0.17 line rather than the
  inspected development commit. Adapter tests assert the runtime version and
  isolate native objects.

Official references: [Splink on PyPI](https://pypi.org/project/splink/),
[Splink source repository](https://github.com/moj-analytical-services/splink),
and [Splink releases](https://github.com/moj-analytical-services/splink/releases).
