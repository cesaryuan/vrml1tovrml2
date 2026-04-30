# vrml1tovrml2

[简体中文](./README.zh-CN.md)

`vrml1tovrml2` is a VRML 1.0 to VRML 2.0 converter designed for Linux-based workflows as a replacement for the legacy Windows `vrml1tovrml2.exe` pipeline. This repository now ships a Rust-native command-line implementation and browser-side WebAssembly build for regression work and future extensions.

## Overview

- Built for migrating and batch-converting existing `.wrl` assets.
- Focused on reproducing the practical workflow of the historical tool rather than byte-for-byte behavior of the original binary.
- Uses a Rust CLI as the default entrypoint, making it suitable for Linux, WSL, and CI environments.
- Includes sample data and checked-in regression cases for ongoing compatibility work.

## Project Status

- The current implementation successfully converts the sample inputs, the checked-in baseline cases, and the vendored public VRML 1.0 sample set in this repository.
- `cargo test --test public_v1_regression` currently passes, covering both exact checked-in baseline comparisons and a broader "must parse and convert successfully" public sample sweep.
- Common VRML 1.0 / Open Inventor style nodes are already covered.
- Coverage work is still ongoing, especially for rare historical extensions and unusual field combinations.

## Quick Start

### Requirements

- Rust toolchain
- Bash

### Run the Converter

The repository root includes a convenience launcher that invokes the Rust CLI:

```bash
./vrml1tovrml2 input_v1.wrl output_v2.wrl
```

If only the input file is provided, the converted result is written to stdout:

```bash
./vrml1tovrml2 input_v1.wrl
```

Enable verbose logging:

```bash
./vrml1tovrml2 --verbose input_v1.wrl output_v2.wrl
```

Show progress for reading, conversion, and writing:

```bash
./vrml1tovrml2 --progress input_v1.wrl output_v2.wrl
```

### Build the Rust Binary

If you want to build first and run afterward:

```bash
cargo build --release
```

## GitHub Pages Online Demo

The repository now includes a browser-side WebAssembly demo under [web](./web) and an automatic GitHub Pages workflow at [deploy-pages.yml](./.github/workflows/deploy-pages.yml).

After enabling GitHub Pages in the repository settings, each push to `main` can publish an online converter at:

```text
https://<owner>.github.io/vrml1tovrml2/
```

The page lets you:

- Upload a VRML 1.0 `.wrl` file directly in the browser
- Load the bundled sample input for a quick smoke test
- Convert to VRML 2.0 with the Rust core compiled to WebAssembly
- Copy or download the converted `.wrl` output without sending files to a server

## Supported and Verified Nodes

- Grouping and hierarchy: `Separator`, `TransformSeparator`, `Group`, `Switch`, `LOD`
- Transforms: `Translation`, `Rotation`, `Scale`, `Transform`, `MatrixTransform`
- Geometry and indexing: `Coordinate3`, `IndexedFaceSet`, `IndexedLineSet`, `PointSet`
- Primitive shapes: `Cube`, `Cone`, `Cylinder`, `Sphere`
- Appearance-related nodes: `Material`, `MaterialBinding`, `Normal`, `NormalBinding`, `ShapeHints`
- Texture-related nodes: `Texture2`, `TextureCoordinate2`, `Texture2Transform`, `Texture2Transformation`
- Text: `AsciiText`, `FontStyle`
- Lights and cameras: `DirectionalLight`, `PointLight`, `SpotLight`, `PerspectiveCamera`, `OrthographicCamera`
- Other nodes: `WWWAnchor`, `WWWInline`
- Shared definitions: `DEF` / `USE`

## Examples and Regression Data

Sample files are available in [examples](./examples):

- Input sample: [examples/sample_v1.wrl](./examples/sample_v1.wrl)
- Output sample: [examples/sample_v2.wrl](./examples/sample_v2.wrl)
- `DEF` / `USE` sample: [examples/sample_defs_v1.wrl](./examples/sample_defs_v1.wrl)

Regression data is organized by case under [wrl/cases](./wrl/cases):

```text
wrl/
  cases/
    <case-name>/
      input.v1.wrl
      baseline.v2.from_exe.wrl
      current.v2.from_rust.wrl
```

Current checked-in cases:

- [sample_minimal](./wrl/cases/sample_minimal)
- [ansys_test_from_ansys_1](./wrl/cases/ansys_test_from_ansys_1)

Public sample inputs are vendored under [tests/data/public_v1_cases](./tests/data/public_v1_cases) to widen parser and converter coverage without requiring golden outputs for every external sample set.

To regenerate the current regression outputs:

```bash
./scripts/regenerate_testset.sh
```

To run the current Rust regression tests:

```bash
cargo test --test public_v1_regression
```

## Repository Layout

- [vrml1tovrml2](./vrml1tovrml2): default command-line launcher
- [src](./src): Rust CLI, parser, converter, and writer implementation
- [examples](./examples): sample input and output files
- [wrl/cases](./wrl/cases): regression cases and baselines
- [tests](./tests): Rust integration tests and vendored public VRML 1.0 sample inputs
- [scripts](./scripts): helper scripts

## Current Limitations

- This is a reimplementation based on reverse-engineered behavior and VRML semantics, not a function-by-function clone of the original DLL.
- The current priority is mainstream node coverage; rare SGI / Cosmo style extension nodes are not fully implemented yet.
- `MatrixTransform` currently targets common affine scenarios, with emphasis on preserving translation and axis-aligned scaling.
- Complex bindings, rare field combinations, and historical compatibility details still need validation against real production samples.
- Some memory optimizations are already in place, but the converter is not yet a fully streaming parse-to-output implementation for extremely large files.

## Before Publishing

- Add a `LICENSE` file before making the repository fully public.
- Add `README` files for more languages if you want multi-language project presentation.
- Keep collecting real `.wrl` samples under [wrl/cases](./wrl/cases) to improve regression coverage over time.
