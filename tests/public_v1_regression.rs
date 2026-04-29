//! Integration tests for checked-in baselines and public VRML 1.0 samples.

use std::error::Error;
use std::fs;
use std::fs::File;
use std::io::BufReader;
use std::path::{Path, PathBuf};

use vrml1tovrml2_rs::{converter, parser, writer::VrmlWriter};

/// Return the repository root used by integration tests.
fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// Convert one VRML 1.0 file into VRML 2.0 text using the Rust pipeline.
fn convert_v1_file(path: &Path) -> Result<String, Box<dyn Error>> {
    let input = File::open(path)?;
    let reader = BufReader::new(input);
    let statements = parser::parse_vrml_reader(reader)?;
    let nodes = converter::convert(&statements)?;
    let mut output = Vec::new();
    VrmlWriter::write_to(&nodes, &mut output)?;
    Ok(String::from_utf8(output)?)
}

/// Collect all vendored public VRML 1.0 sample files under one directory tree.
fn collect_public_v1_files(root: &Path) -> Result<Vec<PathBuf>, Box<dyn Error>> {
    let mut files = Vec::new();
    let mut stack = vec![root.to_path_buf()];

    while let Some(path) = stack.pop() {
        for entry in fs::read_dir(&path)? {
            let entry = entry?;
            let entry_path = entry.path();
            if entry_path.is_dir() {
                stack.push(entry_path);
                continue;
            }
            if entry_path.extension().and_then(|ext| ext.to_str()) == Some("wrl") {
                files.push(entry_path);
            }
        }
    }

    files.sort();
    Ok(files)
}

/// Return a stable repository-relative label for assertion messages.
fn relative_label(path: &Path) -> String {
    path.strip_prefix(repo_root())
        .unwrap_or(path)
        .display()
        .to_string()
}

/// Normalize VRML text so formatting-only differences do not fail baseline tests.
fn normalize_vrml_text(input: &str) -> String {
    let mut normalized = String::new();
    let mut in_string = false;
    let mut escaped = false;
    let mut in_comment = false;
    let mut pending_space = false;

    for character in input.chars() {
        if in_comment {
            if character == '\n' {
                in_comment = false;
                pending_space = true;
            }
            continue;
        }

        if in_string {
            normalized.push(character);
            if escaped {
                escaped = false;
            } else if character == '\\' {
                escaped = true;
            } else if character == '"' {
                in_string = false;
            }
            continue;
        }

        if character == '#' {
            in_comment = true;
            continue;
        }

        if character == '"' {
            if pending_space
                && !normalized.is_empty()
                && !matches!(normalized.chars().last(), Some('{') | Some('[') | Some(','))
            {
                normalized.push(' ');
            }
            pending_space = false;
            in_string = true;
            normalized.push(character);
            continue;
        }

        if character.is_whitespace() {
            pending_space = true;
            continue;
        }

        if matches!(character, '{' | '}' | '[' | ']' | ',') {
            if normalized.ends_with(' ') {
                normalized.pop();
            }
            normalized.push(character);
            pending_space = false;
            continue;
        }

        if pending_space
            && !normalized.is_empty()
            && !matches!(normalized.chars().last(), Some('{') | Some('[') | Some(','))
        {
            normalized.push(' ');
        }
        pending_space = false;
        normalized.push(character);
    }

    normalized.trim().to_owned()
}

#[test]
/// Keep the existing checked-in exe regression cases exact.
fn checked_in_baselines_match_expected_output() -> Result<(), Box<dyn Error>> {
    let root = repo_root();
    let case_names = ["sample_minimal", "ansys_test_from_ansys_1"];

    for case_name in case_names {
        let case_dir = root.join("wrl").join("cases").join(case_name);
        let input_path = case_dir.join("input.v1.wrl");
        let baseline_path = case_dir.join("baseline.v2.from_exe.wrl");
        let actual_output = normalize_vrml_text(&convert_v1_file(&input_path)?);
        let expected_output = normalize_vrml_text(&fs::read_to_string(&baseline_path)?);

        assert_eq!(
            actual_output,
            expected_output,
            "baseline mismatch for {}",
            relative_label(&input_path)
        );
    }

    Ok(())
}

#[test]
/// Ensure all vendored public VRML 1.0 samples at least parse and convert successfully.
fn public_v1_samples_convert_successfully() -> Result<(), Box<dyn Error>> {
    let root = repo_root();
    let public_root = root.join("tests").join("data").join("public_v1_cases");
    let public_files = collect_public_v1_files(&public_root)?;
    let mut failures = Vec::new();

    for input_path in public_files {
        match convert_v1_file(&input_path) {
            Ok(output) => {
                if !output.starts_with("#VRML V2.0 utf8") {
                    failures.push(format!(
                        "{}: output does not start with a VRML 2.0 header",
                        relative_label(&input_path)
                    ));
                }
            }
            Err(error) => {
                failures.push(format!("{}: {error}", relative_label(&input_path)));
            }
        }
    }

    assert!(
        failures.is_empty(),
        "public VRML 1.0 regression failures:\n{}",
        failures.join("\n")
    );
    Ok(())
}
