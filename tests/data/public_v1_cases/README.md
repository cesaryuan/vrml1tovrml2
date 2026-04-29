# Public VRML 1.0 Samples

This directory vendors public `#VRML V1.0 ascii` sample files collected to expand smoke-test coverage for the Rust converter.

Current sources:

- `demo-models/`: copied from `castle-engine/demo-models`
- `Koin3D/`: copied from `YvesBoyadjian/Koin3D`
- `globe-3d/`: copied from `zertovitch/globe-3d`

These files are used as input-only public regression cases. They are intentionally tested as "must parse and convert successfully" samples, not as exact golden-output comparisons, because only a subset of public repositories also publishes trustworthy VRML 2.0 conversion baselines.
