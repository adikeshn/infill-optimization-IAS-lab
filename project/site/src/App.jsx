import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls, Center } from "@react-three/drei";
import { STLLoader } from "three-stdlib";
import { useLoader } from "@react-three/fiber";
import * as THREE from "three";

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

const FACE_COLORS = {
  base: new THREE.Color("#c3cadb"),
  hover: new THREE.Color("#e3e8f5"),
  fixed: new THREE.Color("#315cff"),
  force: new THREE.Color("#f08c00"),
};

// Tessellation from /analyze -> shared geometry pieces. Every B-rep face is
// tessellated separately, so each vertex belongs to exactly one face.
function buildPartMesh({ positions, triangles, face_ids }) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(triangles);
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();

  const vertexFace = new Int32Array(positions.length / 3);
  face_ids.forEach((face, t) => {
    for (let k = 0; k < 3; k++) vertexFace[triangles[3 * t + k]] = face;
  });

  return {
    position: geometry.getAttribute("position"),
    normal: geometry.getAttribute("normal"),
    index: geometry.getIndex(),
    edges: new THREE.EdgesGeometry(geometry, 20),
    vertexFace,
    center: geometry.boundingSphere.center.toArray(),
    radius: geometry.boundingSphere.radius,
  };
}

function ForceGizmo({ center, normal, radius, size }) {
  const n = new THREE.Vector3(...normal).normalize();
  const quaternion = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), n);
  const position = new THREE.Vector3(...center).addScaledVector(n, size * 0.002);
  const arrowLength = size * 0.25;
  const arrowOrigin = new THREE.Vector3(...center).addScaledVector(n, arrowLength);

  return (
    <>
      <group position={position} quaternion={quaternion}>
        <mesh raycast={() => null}>
          <circleGeometry args={[radius, 64]} />
          <meshBasicMaterial color="#f08c00" transparent opacity={0.45} side={THREE.DoubleSide} depthWrite={false} />
        </mesh>
        <mesh raycast={() => null}>
          <ringGeometry args={[radius * 0.9, radius, 64]} />
          <meshBasicMaterial color="#c2410c" side={THREE.DoubleSide} />
        </mesh>
      </group>
      <arrowHelper
        args={[n.clone().negate(), arrowOrigin, arrowLength, "#c2410c", arrowLength * 0.3, arrowLength * 0.15]}
        raycast={() => null}
      />
    </>
  );
}

function PartPicker({ analysis, fixedFaces, force, forceRadius, onPick }) {
  const part = useMemo(() => buildPartMesh(analysis.tessellation), [analysis]);
  const [hovered, setHovered] = useState(null);

  const geometry = useMemo(() => {
    const colors = new Float32Array(part.vertexFace.length * 3);
    part.vertexFace.forEach((face, v) => {
      let color = FACE_COLORS.base;
      if (face === force?.face) color = FACE_COLORS.force;
      else if (fixedFaces.includes(face)) color = FACE_COLORS.fixed;
      else if (face === hovered) color = FACE_COLORS.hover;
      color.toArray(colors, 3 * v);
    });

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", part.position);
    g.setAttribute("normal", part.normal);
    g.setIndex(part.index);
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    return g;
  }, [part, fixedFaces, force, hovered]);

  function faceAt(event) {
    return analysis.tessellation.face_ids[event.faceIndex];
  }

  function handleClick(event) {
    event.stopPropagation();
    if (event.delta > 4) return; // the pointer was dragged to orbit, not clicked

    const face = faceAt(event);
    const point = event.object.worldToLocal(event.point.clone());
    const normal = analysis.faces[face].normal || event.face.normal.toArray();
    onPick(face, point.toArray(), normal);
  }

  const size = part.radius * 2;

  return (
    <Canvas
      camera={{
        up: [0, 0, 1],
        position: [part.radius * 1.2, -part.radius * 2.4, part.radius * 1.2],
        near: part.radius / 100,
        far: part.radius * 100,
        fov: 45,
      }}
    >
      <ambientLight intensity={0.8} />
      <directionalLight position={[1, -2, 3]} intensity={1.4} />
      <directionalLight position={[-2, 1, -1]} intensity={0.5} />
      <group position={part.center.map((c) => -c)}>
        <mesh
          geometry={geometry}
          onClick={handleClick}
          onPointerMove={(event) => {
            event.stopPropagation();
            const face = faceAt(event);
            if (face !== hovered) setHovered(face);
          }}
          onPointerOut={() => setHovered(null)}
        >
          <meshStandardMaterial vertexColors metalness={0.05} roughness={0.7} />
        </mesh>
        <lineSegments geometry={part.edges} raycast={() => null}>
          <lineBasicMaterial color="#4a5568" />
        </lineSegments>
        {force && (
          <ForceGizmo center={force.center} normal={force.normal} radius={forceRadius} size={size} />
        )}
      </group>
      <OrbitControls makeDefault />
    </Canvas>
  );
}

function formatPoint(point) {
  return `(${point.map((c) => c.toFixed(2)).join(", ")})`;
}

function App() {
  const [stepFile, setStepFile] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [analysisStatus, setAnalysisStatus] = useState("");
  const [pickMode, setPickMode] = useState("fixed");
  // The canvas picks up new handlers a frame after React commits, so the click
  // handler reads the mode from a ref to never act on the previous mode.
  const pickModeRef = useRef("fixed");
  const [fixedFaces, setFixedFaces] = useState([]);
  const [force, setForce] = useState(null);
  const [forceDiameter, setForceDiameter] = useState(5);
  const [forceMagnitude, setForceMagnitude] = useState(5);
  const analysisRequest = useRef(0);
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

  async function selectStepFile(file) {
    const request = ++analysisRequest.current;
    setStepFile(file || null);
    setAnalysis(null);
    setFixedFaces([]);
    setForce(null);
    setAnalysisStatus("");
    if (!file) return;

    setAnalysisStatus("Analyzing part...");
    const formData = new FormData();
    formData.append("step_file", file);

    try {
      const response = await fetch("/analyze", { method: "POST", body: formData });
      const data = await response.json();
      if (request !== analysisRequest.current) return;
      if (!response.ok) throw new Error(data.error || "Part analysis failed.");
      setAnalysis(data);
      setAnalysisStatus("");
    } catch (error) {
      if (request === analysisRequest.current) setAnalysisStatus(error.message);
    }
  }

  function changePickMode(mode) {
    pickModeRef.current = mode;
    setPickMode(mode);
  }

  function pickFace(face, point, normal) {
    if (pickModeRef.current === "fixed") {
      setFixedFaces((faces) =>
        faces.includes(face) ? faces.filter((f) => f !== face) : [...faces, face],
      );
    } else {
      setForce({ face, center: point, normal });
    }
  }

  function faceRef(index) {
    return { index, centroid: analysis.faces[index].centroid };
  }

  function loadCaseError() {
    if (!analysis) return "Wait for the part analysis to finish.";
    if (fixedFaces.length === 0) return "Select at least one fixed face.";
    if (!force) return "Pick the force face and point.";
    if (fixedFaces.includes(force.face)) {
      return "The force face can't also be a fixed face.";
    }
    if (!(Number(forceDiameter) > 0) || !(Number(forceMagnitude) > 0)) {
      return "Force diameter and magnitude must be positive.";
    }
    return null;
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
      fixed_faces: fixedFaces.map(faceRef),
      force: {
        face: faceRef(force.face),
        center: force.center,
        diameter: Number(forceDiameter),
        magnitude: Number(forceMagnitude),
      },
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
        setStatus(
          data.status === "failed" && data.error
            ? `Job ${jobName} failed: ${data.error}`
            : `Job ${jobName}: ${data.status}`,
        );

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

    const caseError = loadCaseError();
    if (caseError) {
      setStatus(caseError);
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
              onChange={(e) => selectStepFile(e.target.files[0])}
            />
            <span>{stepFile ? stepFile.name : "Choose base_part.step"}</span>
          </label>

          {analysisStatus && <p className="status pickStatus">{analysisStatus}</p>}

          {analysis && (
            <div className="picker">
              <div className="pickToolbar">
                <div className="modeToggle">
                  <button
                    type="button"
                    className={pickMode === "fixed" ? "modeBtn active" : "modeBtn"}
                    onClick={() => changePickMode("fixed")}
                  >
                    <span className="swatch fixedSwatch" /> Fixed faces
                  </button>
                  <button
                    type="button"
                    className={pickMode === "force" ? "modeBtn active" : "modeBtn"}
                    onClick={() => changePickMode("force")}
                  >
                    <span className="swatch forceSwatch" /> Force
                  </button>
                </div>
                <p className="pickHint">
                  {pickMode === "fixed"
                    ? "Click faces to fix or release them."
                    : "Click the point where the force is applied."}{" "}
                  Drag to orbit.
                </p>
              </div>

              <div className="viewerBox">
                <PartPicker
                  analysis={analysis}
                  fixedFaces={fixedFaces}
                  force={force}
                  forceRadius={Number(forceDiameter) / 2}
                  onPick={pickFace}
                />
              </div>

              <div className="settingsGrid">
                <label>
                  Force Diameter (mm)
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    value={forceDiameter}
                    onChange={(e) => setForceDiameter(e.target.value)}
                  />
                </label>
                <label>
                  Force Magnitude (N)
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    value={forceMagnitude}
                    onChange={(e) => setForceMagnitude(e.target.value)}
                  />
                </label>
              </div>

              <div className="pickSummary">
                <p>
                  <strong>Fixed:</strong>{" "}
                  {fixedFaces.length
                    ? fixedFaces.map((f) => `face ${f}`).join(", ")
                    : "none"}
                  {fixedFaces.length > 0 && (
                    <button type="button" className="linkBtn" onClick={() => setFixedFaces([])}>
                      clear
                    </button>
                  )}
                </p>
                <p>
                  <strong>Force:</strong>{" "}
                  {force
                    ? `face ${force.face} at ${formatPoint(force.center)}, pushing into the face`
                    : "none"}
                  {force && (
                    <button type="button" className="linkBtn" onClick={() => setForce(null)}>
                      clear
                    </button>
                  )}
                </p>
                {force && fixedFaces.includes(force.face) && (
                  <p className="pickWarning">The force face is also selected as fixed.</p>
                )}
              </div>
            </div>
          )}
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
