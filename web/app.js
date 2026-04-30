import init, { convertVrmlText } from "./pkg/vrml1tovrml2.js";

const state = {
  wasmReady: false,
  sourceName: "input_v1.wrl",
};

const elements = {
  statusBadge: document.querySelector("#status-badge"),
  statusLine: document.querySelector("#status-line"),
  sourceFile: document.querySelector("#source-file"),
  sourceText: document.querySelector("#source-text"),
  outputText: document.querySelector("#output-text"),
  loadSample: document.querySelector("#load-sample"),
  convertButton: document.querySelector("#convert-button"),
  copyButton: document.querySelector("#copy-button"),
  downloadButton: document.querySelector("#download-button"),
};

/// Render the current status message into the hero badge and tool area.
function setStatus(message, tone = "info") {
  elements.statusBadge.textContent = message;
  elements.statusLine.textContent = message;
  elements.statusBadge.dataset.tone = tone;
}

/// Return a stable VRML 2.0 output filename based on the current source file.
function buildOutputName(sourceName) {
  const normalizedName = sourceName.trim() || "converted";
  const baseName = normalizedName.replace(/\.[^/.]+$/, "");
  return `${baseName}.v2.wrl`;
}

/// Refresh button disabled states after input, output, or WASM readiness changes.
function syncButtons() {
  const hasInput = elements.sourceText.value.trim().length > 0;
  const hasOutput = elements.outputText.value.trim().length > 0;

  elements.convertButton.disabled = !state.wasmReady || !hasInput;
  elements.copyButton.disabled = !hasOutput;
  elements.downloadButton.disabled = !hasOutput;
}

/// Store source text and keep filename-related UI state in sync.
function setInputValue(value, fileName = state.sourceName) {
  state.sourceName = fileName;
  elements.sourceText.value = value;
  syncButtons();
}

/// Store converted output text and update download-related UI affordances.
function setOutputValue(value) {
  elements.outputText.value = value;
  syncButtons();
}

/// Read a selected file as UTF-8 text for browser-side conversion.
function readFileText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read the selected file."));
    reader.readAsText(file, "utf-8");
  });
}

/// Load the bundled VRML sample into the input editor.
async function loadSample() {
  setStatus("Loading the bundled sample scene...", "info");

  try {
    const response = await fetch("./examples/sample_v1.wrl");
    if (!response.ok) {
      throw new Error(`Sample request failed with ${response.status}.`);
    }

    const sampleText = await response.text();
    setInputValue(sampleText, "sample_v1.wrl");
    setOutputValue("");
    setStatus("Sample loaded. Click convert to generate VRML 2.0 output.", "ready");
  } catch (error) {
    console.error("Failed to load sample input.", error);
    setStatus(`Sample load failed: ${error.message}`, "error");
  }
}

/// Convert the current VRML 1.0 source text through the WASM bridge.
async function runConversion() {
  const sourceText = elements.sourceText.value.trim();
  if (!sourceText) {
    setStatus("Paste or upload a VRML 1.0 file before converting.", "error");
    return;
  }

  setStatus("Converting in the browser...", "busy");

  try {
    const convertedText = convertVrmlText(sourceText);
    setOutputValue(convertedText);
    setStatus(`Conversion finished. Output file: ${buildOutputName(state.sourceName)}`, "ready");
  } catch (error) {
    console.error("Browser conversion failed.", error);
    setOutputValue("");
    setStatus(`Conversion failed: ${error.message}`, "error");
  }
}

/// Download the converted VRML 2.0 text as a local `.wrl` file.
function downloadOutput() {
  const outputText = elements.outputText.value;
  if (!outputText) {
    return;
  }

  const blob = new Blob([outputText], { type: "model/vrml;charset=utf-8" });
  const downloadUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");

  anchor.href = downloadUrl;
  anchor.download = buildOutputName(state.sourceName);
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(downloadUrl);
}

/// Copy the current VRML 2.0 output to the system clipboard when available.
async function copyOutput() {
  const outputText = elements.outputText.value;
  if (!outputText) {
    return;
  }

  try {
    await navigator.clipboard.writeText(outputText);
    setStatus("Converted output copied to the clipboard.", "ready");
  } catch (error) {
    console.error("Clipboard copy failed.", error);
    setStatus(`Copy failed: ${error.message}`, "error");
  }
}

/// Initialize the WASM runtime and wire all UI event handlers once.
async function main() {
  syncButtons();

  elements.loadSample.addEventListener("click", loadSample);
  elements.convertButton.addEventListener("click", runConversion);
  elements.copyButton.addEventListener("click", copyOutput);
  elements.downloadButton.addEventListener("click", downloadOutput);
  elements.sourceText.addEventListener("input", () => {
    setOutputValue("");
    setStatus("Input updated. Click convert to refresh the VRML 2.0 output.", "info");
  });
  elements.sourceFile.addEventListener("change", async (event) => {
    const [file] = event.target.files ?? [];
    if (!file) {
      return;
    }

    setStatus(`Reading ${file.name}...`, "info");

    try {
      const fileText = await readFileText(file);
      setInputValue(fileText, file.name);
      setOutputValue("");
      setStatus(`Loaded ${file.name}. Click convert to continue.`, "ready");
    } catch (error) {
      console.error("File loading failed.", error);
      setStatus(`File load failed: ${error.message}`, "error");
    }
  });

  try {
    await init();
    state.wasmReady = true;
    syncButtons();
    setStatus("WASM ready. Upload a file or load the sample to start.", "ready");
  } catch (error) {
    console.error("WASM initialization failed.", error);
    setStatus(`WASM initialization failed: ${error.message}`, "error");
  }
}

main();
