import { useEffect, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls, Center } from "@react-three/drei";
import { STLLoader } from "three-stdlib";
import { useLoader } from "@react-three/fiber";

const INFILLS = [
  { label: "Grid", value: "grid" },
  { label: "Honeycomb", value: "honey" },
  { label: "Finray", value: "finr" },
  { label: "Triangle", value: "tri" },
];

const DEFAULT_ANGLES = {
  grid: 45,
  honey: -45,
  finr: -60,
  tri: 45,
};

function makeRow(infill = "grid") {
  return {
    infill,
    angle: DEFAULT_ANGLES[infill],
    densityMode: "single",
    density: 20,
    densityStart: 10,
    densityEnd: 20,
    densityStep: 2,
  };
}

function expandDensities(row) {
  if (row.densityMode !== "range") {
    return [Number(row.density)];
  }

  const start = Number(row.densityStart);
  const end = Number(row.densityEnd);
  const step = Number(row.densityStep);

  if (!(step > 0) || end < start) {
    return [];
  }

  const densities = [];
  const stepCount = Math.floor((end - start) / step + 1e-9);
  for (let i = 0; i <= stepCount; i++) {
    densities.push(Math.round((start + i * step) * 100) / 100);
  }
  return densities;
}

function StlModel({ url }) {
  const geometry = useLoader(STLLoader, url);

  return (
    <Center>
      <mesh geometry={geometry}>
        <meshStandardMaterial color="#8aa4ff" metalness={0.1} roughness={0.5} />
      </mesh>
    </Center>
  );
}

function App() {
  const [stepFile, setStepFile] = useState(null);
  const [meshSize, setMeshSize] = useState(1.0);
  const [outThickness, setOutThickness] = useState(0.87);
  const [infThickness, setInfThickness] = useState(0.45);
  const [viewerUrl, setViewerUrl] = useState(null);
  const [viewerTitle, setViewerTitle] = useState("");
  const [rows, setRows] = useState([makeRow()]);

  const [status, setStatus] = useState("");
  const [result, setResult] = useState(null);
  const [currentJob, setCurrentJob] = useState(null);
  const [polling, setPolling] = useState(false);
  const [previousJobs, setPreviousJobs] = useState([]);

  useEffect(() => {
    loadPreviousJobs();
  }, []);

  async function loadPreviousJobs() {
    try {
      const response = await fetch("/jobs");
      const data = await response.json();
      setPreviousJobs(data.jobs || []);
    } catch {
      setPreviousJobs([]);
    }
  }

  function addRow() {
    setRows([...rows, makeRow()]);
  }

  function getStepDownloadUrl(jobName, designName) {
    return `/jobs/${jobName}/infills/${designName}.step/download`;
  }

  function updateRow(index, key, value) {
    const newRows = [...rows];
    newRows[index] = { ...newRows[index], [key]: value };
    if (key === "infill") {
      newRows[index].angle = DEFAULT_ANGLES[value];
    }
    setRows(newRows);
  }

  function removeRow(index) {
    if (rows.length === 1) return;
    setRows(rows.filter((_, i) => i !== index));
  }

  function buildSimSpace() {
    const infills = [];

    rows.forEach((row) => {
      expandDensities(row).forEach((density) => {
        infills.push({
          type: row.infill,
          density,
          angle: Number(row.angle),
        });
      });
    });

    return {
      infills,
      mesh_size: Number(meshSize),
      out_thickness: Number(outThickness),
      inf_thickness: Number(infThickness),
    };
  }

  function getStepUrl(jobName, designName) {
    return `/jobs/${jobName}/infills/${designName}.step`;
  }

  async function pollJob(jobName) {
    setPolling(true);

    const intervalId = setInterval(async () => {
      try {
        const response = await fetch(`/jobs/${jobName}`);
        const data = await response.json();

        setResult(data);
        setStatus(`Job ${jobName}: ${data.status}`);

        if (data.status === "complete" || data.status === "failed") {
          clearInterval(intervalId);
          setPolling(false);

          if (data.status === "complete") {
            loadPreviousJobs();
          }
        }
      } catch (error) {
        clearInterval(intervalId);
        setPolling(false);
        setStatus("Could not check job status.");
        setResult({ error: error.message });
      }
    }, 3000);
  }

  async function submitJob(event) {
    event.preventDefault();

    if (!stepFile) {
      setStatus("Please upload a STEP file first.");
      return;
    }

    const simSpace = buildSimSpace();
    if (simSpace.infills.length === 0) {
      setStatus("No valid density values to run. Check your density ranges.");
      return;
    }

    const formData = new FormData();
    formData.append("step_file", stepFile);
    formData.append("sim_space", JSON.stringify(simSpace));

    setStatus("Submitting job...");
    setResult(null);
    setCurrentJob(null);

    try {
      const response = await fetch("/run", {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (!response.ok) {
        setStatus("Failed to queue job.");
        setResult(data);
        return;
      }

      setCurrentJob(data.job);
      setStatus(`Job ${data.job} queued.`);
      setResult(data);

      pollJob(data.job);
    } catch (error) {
      setStatus("Could not connect to backend.");
      setResult({ error: error.message });
    }
  }

  function RankingTable({ jobName, metrics }) {
    if (!metrics) {
      return <p>No saved metrics for this job.</p>;
    }

    return (
      <table>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Design</th>
            <th>Pseudo CGS</th>
            <th>Displacement</th>
            <th>Stress</th>
            <th>STEP</th>
          </tr>
        </thead>

        <tbody>
          {metrics.map((row, index) => (
            <tr key={`${jobName}-${index}`}>
              <td>#{index + 1}</td>
              <td>{row[0]}</td>
              <td>{Number(row[1]).toFixed(4)}</td>
              <td>{Number(row[2]).toFixed(4)}</td>
              <td>{Number(row[3]).toFixed(4)}</td>
              <td>
                <td>
                  <div className="buttonGroup">
                    <button
                      type="button"
                      className="viewerBtn"
                      onClick={() => {
                        setViewerUrl(getStepUrl(jobName, row[0]));
                        setViewerTitle(`${jobName} - ${row[0]}`);
                      }}
                    >
                      View Model
                    </button>

                    <a
                      className="downloadBtn"
                      href={getStepDownloadUrl(jobName, row[0])}
                      download
                    >
                      Download STEP
                    </a>
                  </div>
                </td>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return (
    <main className="page">
      <section className="hero">
        <p className="tag">Infill FEA Tool</p>
        <h1>Batch simulation setup</h1>
        <p>
          Upload one STEP file, test multiple infill-density combinations, and
          view current or previous job rankings.
        </p>
      </section>

      <form className="panel" onSubmit={submitJob}>
        <section className="card">
          <h2>Base Part</h2>

          <label className="uploadBox">
            <input
              type="file"
              accept=".step,.stp"
              onChange={(e) => setStepFile(e.target.files[0])}
            />
            <span>{stepFile ? stepFile.name : "Choose base_part.step"}</span>
          </label>
        </section>

        <section className="card">
          <div className="cardHeader">
            <h2>Design Space</h2>
            <button type="button" className="secondaryBtn" onClick={addRow}>
              + Add Entry
            </button>
          </div>

          <table>
            <thead>
              <tr>
                <th>Infill</th>
                <th>Angle (deg)</th>
                <th>Density Mode</th>
                <th>Density</th>
                <th></th>
              </tr>
            </thead>

            <tbody>
              {rows.map((row, index) => {
                const previewCount = expandDensities(row).length;

                return (
                  <tr key={index}>
                    <td>
                      <select
                        value={row.infill}
                        onChange={(e) =>
                          updateRow(index, "infill", e.target.value)
                        }
                      >
                        {INFILLS.map((infill) => (
                          <option key={infill.value} value={infill.value}>
                            {infill.label}
                          </option>
                        ))}
                      </select>
                    </td>

                    <td>
                      <input
                        type="number"
                        step="0.1"
                        value={row.angle}
                        onChange={(e) =>
                          updateRow(index, "angle", e.target.value)
                        }
                      />
                    </td>

                    <td>
                      <select
                        value={row.densityMode}
                        onChange={(e) =>
                          updateRow(index, "densityMode", e.target.value)
                        }
                      >
                        <option value="single">Single</option>
                        <option value="range">Range</option>
                      </select>
                    </td>

                    <td>
                      {row.densityMode === "range" ? (
                        <div className="densityRange">
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="0.01"
                            title="Start density"
                            value={row.densityStart}
                            onChange={(e) =>
                              updateRow(index, "densityStart", e.target.value)
                            }
                          />
                          <span>to</span>
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="0.01"
                            title="End density"
                            value={row.densityEnd}
                            onChange={(e) =>
                              updateRow(index, "densityEnd", e.target.value)
                            }
                          />
                          <span>step</span>
                          <input
                            type="number"
                            min="0.01"
                            max="100"
                            step="0.01"
                            title="Step size"
                            value={row.densityStep}
                            onChange={(e) =>
                              updateRow(index, "densityStep", e.target.value)
                            }
                          />
                          <span className="densityRangeCount">
                            ({previewCount} run{previewCount === 1 ? "" : "s"})
                          </span>
                        </div>
                      ) : (
                        <input
                          type="number"
                          min="0"
                          max="100"
                          step="0.01"
                          value={row.density}
                          onChange={(e) =>
                            updateRow(index, "density", e.target.value)
                          }
                        />
                      )}
                    </td>

                    <td>
                      <button
                        type="button"
                        className="deleteBtn"
                        onClick={() => removeRow(index)}
                        disabled={rows.length === 1}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section className="card">
          <h2>Global Simulation Settings</h2>

          <div className="settingsGrid">
            <label>
              Mesh Size
              <input
                type="number"
                step="0.01"
                value={meshSize}
                onChange={(e) => setMeshSize(e.target.value)}
              />
            </label>

            <label>
              Outline Thickness
              <input
                type="number"
                step="0.01"
                value={outThickness}
                onChange={(e) => setOutThickness(e.target.value)}
              />
            </label>

            <label>
              Infill Thickness
              <input
                type="number"
                step="0.01"
                value={infThickness}
                onChange={(e) => setInfThickness(e.target.value)}
              />
            </label>
          </div>
        </section>

        <section className="card submitCard">
          <button type="submit" className="primaryBtn" disabled={polling}>
            {polling ? "Running..." : "Run Job"}
          </button>

          {status && <p className="status">{status}</p>}
        </section>
      </form>

      {result?.metrics && (
        <section className="resultsCard">
          <h2>Current Job Ranking</h2>
          <RankingTable
            jobName={result.job || currentJob}
            metrics={result.metrics}
          />
        </section>
      )}

      {viewerUrl && (
        <section className="resultsCard">
          <div className="cardHeader">
            <h2>Model Viewer: {viewerTitle}</h2>
            <button
              type="button"
              className="secondaryBtn"
              onClick={() => {
                setViewerUrl(null);
                setViewerTitle("");
              }}
            >
              Close
            </button>
          </div>

          <div className="viewerBox">
            <Canvas camera={{ position: [0, -80, 60], fov: 45 }}>
              <ambientLight intensity={0.6} />
              <directionalLight position={[10, 10, 10]} intensity={1.2} />
              <StlModel url={viewerUrl} />
              <OrbitControls />
            </Canvas>
          </div>
        </section>
      )}

      <section className="resultsCard">
        <div className="cardHeader">
          <h2>Previous Jobs</h2>
          <button
            type="button"
            className="secondaryBtn"
            onClick={loadPreviousJobs}
          >
            Refresh
          </button>
        </div>

        {previousJobs.length === 0 ? (
          <p>No previous jobs found.</p>
        ) : (
          previousJobs.map((job) => (
            <details key={job.job} className="jobDetails">
              <summary>{job.job}</summary>
              <RankingTable jobName={job.job} metrics={job.metrics} />
            </details>
          ))
        )}
      </section>
    </main>
  );
}

export default App;
