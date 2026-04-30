#!/usr/bin/env bash
# Rebuild current converter outputs and diff them against the checked-in baseline set.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_case() {
  # Rebuild one checked-in case and show the diff to its saved exe baseline.
  local case_dir="$1"
  local input_file="${ROOT_DIR}/wrl/cases/${case_dir}/input.v1.wrl"
  local current_file="${ROOT_DIR}/wrl/cases/${case_dir}/current.v2.from_rust.wrl"
  local baseline_file="${ROOT_DIR}/wrl/cases/${case_dir}/baseline.v2.from_exe.wrl"

  echo "[info] regenerating case: ${case_dir}"
  "${ROOT_DIR}/vrml1tovrml2" "${input_file}" "${current_file}"

  echo "[info] diff: ${case_dir}"
  diff -u "${baseline_file}" "${current_file}" || true
}

run_case "sample_minimal"
run_case "ansys_test_from_ansys_1"
