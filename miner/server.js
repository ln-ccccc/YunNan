import express from 'express';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import dotenv from 'dotenv';
import xlsx from 'xlsx';
import { DOMParser } from '@xmldom/xmldom';
import tj from '@mapbox/togeojson';
import geoviewRoutes from './routes/geoview.js';
import { createProjectRoutes } from './routes/projects.js';
import { authBackend } from './services/authBackend.js';
import { relayBackendResponse, requireMinerAuth } from './services/authProxy.js';
import { buildChangeMatrixAssetPath } from './services/changeMatrixAssets.js';
import { inferenceBackend } from './services/inferenceBackend.js';
import { buildIndicesPayload, calculateStats, INDEX_SOURCE_FILES } from './services/indexSeries.js';
import { saveKmlUpload } from './services/kmlUpload.js';
import { buildTrendReport } from './services/trendReport.js';

dotenv.config();

const app = express();
const port = process.env.PORT ? Number(process.env.PORT) : 8000;
const startupCwd = process.cwd();
console.log(`[Startup] miner cwd=${startupCwd}`);
if (/wsl|\\\\wsl\\.localhost/i.test(startupCwd)) {
  console.warn('[Startup] Warning: running from WSL path; expected E:\\GeoView\\miner');
}

// Enable CORS and JSON parsing
app.use(cors());
app.use(express.json({ limit: '60mb' }));
const authGuard = requireMinerAuth({ sessionApi: authBackend.session });
app.use('/api/geoview', authGuard, geoviewRoutes);

app.post('/api/auth/login', async (req, res) => {
  relayBackendResponse(res, await authBackend.login(req.body || {}));
});

app.get('/api/auth/session', async (req, res) => {
  relayBackendResponse(res, await authBackend.session(req.headers.cookie || ''));
});

app.post('/api/auth/logout', async (req, res) => {
  relayBackendResponse(res, await authBackend.logout(req.headers.cookie || ''));
});

app.use('/api/projects', authGuard, createProjectRoutes());

app.get('/tiles/:z/:x/:y.png', (req, res) => {
  return res.status(410).json({ error: '全局瓦片接口已停用，请使用项目瓦片地址' });
});

const projectStorageRoot = path.resolve(process.env.PROJECT_STORAGE_ROOT || '/project_storage');
app.get('/tiles/projects/:projectId/:resourceId/:z/:x/:y.png', (req, res) => {
  const segments = ['projectId', 'resourceId', 'z', 'x', 'y'].map((key) => String(req.params[key] || ''));
  if (!segments.every((value) => /^\d+$/.test(value))) {
    return res.status(400).json({ error: 'Invalid tile path' });
  }
  const [projectId, resourceId, z, x, y] = segments;
  const tileRoot = path.resolve(projectStorageRoot, 'projects', projectId, 'tiles', resourceId);
  if (!fs.existsSync(path.join(tileRoot, '.active'))) {
    return res.status(404).json({ error: 'Project basemap is not active' });
  }
  const tilePath = path.resolve(tileRoot, z, x, `${y}.png`);
  if (!tilePath.startsWith(`${tileRoot}${path.sep}`) || !fs.existsSync(tilePath)) {
    return res.status(404).json({ error: 'Project tile not found' });
  }
  return res.sendFile(tilePath);
});

const changeMatrixStaticDir = path.resolve(process.cwd(), 'change_matrix_outputs');
app.use('/change-matrix-outputs', authGuard, express.static(changeMatrixStaticDir));

const defaultOutputRoot = path.resolve(process.cwd(), 'change_matrix_outputs');
const defaultKmlUploadRoot = path.resolve(process.cwd(), 'uploads', 'kml');

function getLandTypeList(value) {
  const raw = String(value || '').trim();
  if (!raw) return ['未知'];
  const types = raw
    .split(/[,\uFF0C;；/、]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  return Array.from(new Set(types.length ? types : ['未知']));
}

function normalizePathInput(v) {
  if (!v) return '';
  return String(v).trim().replace(/^["']|["']$/g, '');
}

function parseChangedAreaKm2FromMatrixCsv(csvPath) {
  if (!fs.existsSync(csvPath)) {
    return { has_change_matrix: false, changed_area_km2: 0 };
  }
  const content = fs.readFileSync(csvPath, 'utf-8').trim();
  if (!content) {
    return { has_change_matrix: false, changed_area_km2: 0 };
  }
  const lines = content.split('\n').map((line) => line.trim()).filter(Boolean);
  if (lines.length < 2) {
    return { has_change_matrix: false, changed_area_km2: 0 };
  }

  let changedArea = 0;
  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(',');
    const rowValues = parts.slice(1).map((v) => Number(v));
    for (let j = 0; j < rowValues.length; j++) {
      const val = rowValues[j];
      if (!Number.isFinite(val)) continue;
      if (j !== (i - 1)) changedArea += val;
    }
  }
  return { has_change_matrix: true, changed_area_km2: changedArea };
}

function parseOffDiagonalFromMatrixCsv(csvPath) {
  if (!fs.existsSync(csvPath)) {
    return { exists: false, off_diagonal_sum: 0 };
  }
  const content = fs.readFileSync(csvPath, 'utf-8').trim();
  if (!content) {
    return { exists: false, off_diagonal_sum: 0 };
  }
  const lines = content.split('\n').map((line) => line.trim()).filter(Boolean);
  if (lines.length < 2) {
    return { exists: false, off_diagonal_sum: 0 };
  }
  let offDiag = 0;
  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(',');
    const rowValues = parts.slice(1).map((v) => Number(v));
    for (let j = 0; j < rowValues.length; j++) {
      const val = rowValues[j];
      if (!Number.isFinite(val)) continue;
      if (j !== (i - 1)) offDiag += val;
    }
  }
  return { exists: true, off_diagonal_sum: offDiag };
}

function readResolutionFromOutputDir(outputDir) {
  const metaCandidates = ['resolution.json', 'meta.json', 'inference_meta.json'];
  for (const f of metaCandidates) {
    const fp = path.join(outputDir, f);
    if (!fs.existsSync(fp)) continue;
    try {
      const obj = JSON.parse(fs.readFileSync(fp, 'utf-8'));
      const rx = Number(obj?.resolution?.x ?? obj?.resolution_x ?? obj?.res_x ?? obj?.pixel_size_x ?? obj?.pixelSizeX);
      const ry = Number(obj?.resolution?.y ?? obj?.resolution_y ?? obj?.res_y ?? obj?.pixel_size_y ?? obj?.pixelSizeY);
      if (Number.isFinite(rx) && Number.isFinite(ry) && rx !== 0 && ry !== 0) {
        return { res_x: rx, res_y: ry };
      }
    } catch (_) {}
  }
  return null;
}

function computeMineChangedArea(fidRaw) {
  const fid = String(fidRaw);
  const outputDir = path.resolve(process.cwd(), 'change_matrix_outputs', fid);
  const km2CsvPath = path.join(outputDir, 'change_matrix_km2.csv');
  const pxCsvPath = path.join(outputDir, 'change_matrix_pixels.csv');

  const km2Res = parseOffDiagonalFromMatrixCsv(km2CsvPath);
  if (km2Res.exists) {
    return {
      changed_area_km2: Number(km2Res.off_diagonal_sum.toFixed(6)),
      area_source: 'km2_matrix',
      has_inference_output: true,
      has_change_matrix: true
    };
  }

  const pxRes = parseOffDiagonalFromMatrixCsv(pxCsvPath);
  if (!pxRes.exists) {
    return {
      changed_area_km2: null,
      area_source: 'none',
      has_inference_output: false,
      has_change_matrix: false
    };
  }

  const resolution = readResolutionFromOutputDir(outputDir);
  const fallbackRes = Number(process.env.DEFAULT_PIXEL_RES_METERS || 1);
  const hasFallbackRes = Number.isFinite(fallbackRes) && fallbackRes > 0;
  if (!resolution && !hasFallbackRes) {
    return {
      changed_area_km2: null,
      area_source: 'none',
      has_inference_output: true,
      has_change_matrix: true
    };
  }

  const resX = resolution ? resolution.res_x : fallbackRes;
  const resY = resolution ? resolution.res_y : fallbackRes;
  const pixelAreaM2 = Math.abs(resX * resY);
  const changedKm2 = (pxRes.off_diagonal_sum * pixelAreaM2) / 1e6;
  return {
    changed_area_km2: Number(changedKm2.toFixed(6)),
    area_source: resolution ? 'pixel_resolution' : 'pixel_resolution_assumed_1m',
    has_inference_output: true,
    has_change_matrix: true
  };
}

// In-memory data storage
let minesData = []; // Array of GeoJSON features
let ndviData = {};  // Object mapping FID -> Array of {year, value}
let ndbiData = {};
let ndwiData = {};
let ndsiData = {};
let indexAvailability = {
  ndvi: { available: false, source_file: INDEX_SOURCE_FILES.ndvi, reason: 'not_loaded' },
  ndbi: { available: false, source_file: INDEX_SOURCE_FILES.ndbi, reason: 'not_loaded' },
  ndwi: { available: false, source_file: INDEX_SOURCE_FILES.ndwi, reason: 'not_loaded' },
  ndsi: { available: false, source_file: INDEX_SOURCE_FILES.ndsi, reason: 'not_loaded' },
};

// --- Helper functions for Excel parsing ---
function detectColumns(headerRow, valueRegex) {
  const header = headerRow.map(h => String(h || '').trim());
  const fidCol = header.find(h => /^(fid|fid_1)$/i.test(h));
  const yearCol = header.find(h => /^year$/i.test(h));
  const valCol = header.find(h => valueRegex.test(h));
  const yearHeaders = header.filter(h => /(19|20)\d{2}/.test(h));
  return { fidCol, yearCol, valCol, yearHeaders, header };
}

function rowsFromSheet(sheet, valueRegex) {
  const aoa = xlsx.utils.sheet_to_json(sheet, { header: 1, defval: null });
  if (!aoa.length) return [];
  const headerRow = aoa[0];
  const { fidCol, yearCol, valCol, yearHeaders, header } = detectColumns(headerRow, valueRegex);
  const dataRows = aoa.slice(1);

  const rows = [];
  if (fidCol && yearCol && valCol) {
    // tidy format
    const idxFID = header.indexOf(fidCol);
    const idxYear = header.indexOf(yearCol);
    const idxVal = header.indexOf(valCol);
    for (const r of dataRows) {
      const fid = Number(r[idxFID]);
      const year = Number(r[idxYear]);
      const val = r[idxVal] != null ? Number(r[idxVal]) : null;
      if (!Number.isFinite(fid) || !Number.isFinite(year) || !Number.isFinite(val)) continue;
      rows.push({ fid, year, value: val });
    }
  } else if (fidCol && yearHeaders.length) {
    // wide format
    const idxFID = header.indexOf(fidCol);
    const yearIdxMap = yearHeaders.reduce((acc, y) => { acc[y] = header.indexOf(y); return acc; }, {});
    for (const r of dataRows) {
      const fid = Number(r[idxFID]);
      if (!Number.isFinite(fid)) continue;
      for (const yStr of yearHeaders) {
        // Extract year from header like "2023" or "NDVI_2023"
        const yMatch = yStr.match(/(19|20)\d{2}/);
        const year = yMatch ? Number(yMatch[0]) : null;
        if (!year) continue;
        const val = r[yearIdxMap[yStr]] != null ? Number(r[yearIdxMap[yStr]]) : null;
        if (Number.isFinite(val)) {
          rows.push({ fid, year, value: val });
        }
      }
    }
  }
  return rows;
}

async function loadIndexData(filePath, valueRegex) {
  const fullPath = path.resolve(process.cwd(), filePath);
  const result = {
    dataMap: {},
    available: false,
    source_file: path.basename(filePath),
    reason: 'missing_source_file',
  };
  if (fs.existsSync(fullPath)) {
    try {
      const workbook = xlsx.readFile(fullPath);
      const sheetName = workbook.SheetNames[0];
      const sheet = workbook.Sheets[sheetName];
      const rows = rowsFromSheet(sheet, valueRegex);
      
      // Group by FID
      for (const r of rows) {
        if (!result.dataMap[r.fid]) result.dataMap[r.fid] = [];
        result.dataMap[r.fid].push({ year: r.year, value: r.value });
      }
      
      // Sort by year for each FID
      for (const fid in result.dataMap) {
        result.dataMap[fid].sort((a, b) => a.year - b.year);
      }
      result.available = true;
      result.reason = null;
      console.log(`Loaded data from ${path.basename(filePath)} for ${Object.keys(result.dataMap).length} mines.`);
    } catch (e) {
      result.reason = 'load_failed';
      console.error(`Failed to load ${filePath}:`, e);
    }
  } else {
    console.warn(`File not found: ${filePath}`);
  }
  return result;
}

// --- Initialization function ---
async function initData() {
  // 1. Load KML and convert to GeoJSON
  const kmlPath = path.resolve(process.cwd(), 'yunnan.kml');
  if (fs.existsSync(kmlPath)) {
    try {
      const kmlContent = fs.readFileSync(kmlPath, 'utf-8');
      const kmlDom = new DOMParser().parseFromString(kmlContent);
      const converted = tj.kml(kmlDom);
      
      if (converted && converted.features && Array.isArray(converted.features)) {
        minesData = converted.features;
        // Post-processing to ensure numeric fields are numbers and normalize status
        minesData.forEach(f => {
            if (f.properties) {
                // Ensure FID_1 is number if possible
                if (f.properties.FID_1) f.properties.FID_1 = Number(f.properties.FID_1);
                
                // Ensure Area is number
                const area = f.properties.TBTYMJ || f.properties.TBTYMJ_1 || f.properties.SHAPE_Area;
                if (area) f.properties.area = Number(area);
                
                // Normalize Status
                const status = String(f.properties.HFZLQK || '').trim();
                if (/(未治|未恢复|未治理)/.test(status)) f.properties.status_normalized = 'untreated';
                else if (/(已|治理|复垦|恢复)/.test(status)) f.properties.status_normalized = 'treated';
                else f.properties.status_normalized = 'unknown';
            }
        });
        console.log(`Loaded ${minesData.length} mines from yunnan.kml.`);
        if (minesData.length > 0) console.log('Sample properties:', minesData[0].properties);
      }
    } catch (e) {
      console.error('Failed to parse KML:', e);
    }
  } else {
    console.warn('yunnan.kml not found.');
  }

  // 2. Load Indices Data
  const ndviResult = await loadIndexData(INDEX_SOURCE_FILES.ndvi, /^(ndvi|ndvi_value)$/i);
  const ndbiResult = await loadIndexData(INDEX_SOURCE_FILES.ndbi, /^(ndbi|ndbi_value|mean_ndbi|mean)$/i);
  const ndwiResult = await loadIndexData(INDEX_SOURCE_FILES.ndwi, /^(ndwi|ndwi_value|mean_ndwi|mean)$/i);
  const ndsiResult = await loadIndexData(INDEX_SOURCE_FILES.ndsi, /^(ndsi|ndsi_value|mean_ndsi|mean)$/i);

  ndviData = ndviResult.dataMap;
  ndbiData = ndbiResult.dataMap;
  ndwiData = ndwiResult.dataMap;
  ndsiData = ndsiResult.dataMap;
  indexAvailability = {
    ndvi: { available: ndviResult.available, source_file: ndviResult.source_file, reason: ndviResult.reason },
    ndbi: { available: ndbiResult.available, source_file: ndbiResult.source_file, reason: ndbiResult.reason },
    ndwi: { available: ndwiResult.available, source_file: ndwiResult.source_file, reason: ndwiResult.reason },
    ndsi: { available: ndsiResult.available, source_file: ndsiResult.source_file, reason: ndsiResult.reason },
  };
}

// Global Dali files are intentionally not loaded. Project map endpoints read
// only the active resources registered for the requested project.

// --- API Endpoints ---

const retiredGlobalMapEndpoints = [
  '/api/stats',
  '/api/geojson',
  '/api/mines/search',
  '/api/mines/indices',
  '/api/mines/change-matrix',
  '/api/mines/change-area-summary',
  '/api/mines/trend-report',
  '/api/mines/ndvi',
];
app.all(retiredGlobalMapEndpoints, authGuard, (_req, res) => {
  res.status(410).json({ error: '全局地图接口已停用，请使用带项目 ID 的接口' });
});

// Get Global Statistics
app.get('/api/stats', authGuard, (req, res) => {
  // 1. Mine Area Statistics & Global Aggregations
    let totalArea = 0;
    let treatedCount = 0;
    let untreatedCount = 0;
    let smallMines = 0;  // < 1 km^2
    let mediumMines = 0; // 1-2 km^2
    let largeMines = 0;  // > 2 km^2
    
    // Aggregations for User Requested Data
    const miningMethodStats = {}; // KCFS
    const closingYearStats = {}; // GBND
    const restorationStatusStats = {}; // HFZLQK (Simplified)
    const restorationMethodStats = {}; // NXFFS
    const damageTypeStats = {}; // STWT
    const landTypeStats = {}; // NXFFX
    let totalChangedAreaKm2 = 0;
    let validMineCount = 0;
    let missingMineCount = 0;
    const mineChangeAreaList = [];

    // Helper for area calculation (Shoelace formula for planar projection approximation)
    const calculateArea = (coords) => {
      if (!coords || coords.length < 1) return 0;
      let area = 0;
      const ring = coords[0]; // Outer ring
      if (ring.length < 3) return 0;

      // Approximate conversion to meters
      // Center lat for scale
      const centerLat = ring[0][1] * Math.PI / 180;
      const mPerDegLat = 111319.9;
      const mPerDegLon = 111319.9 * Math.cos(centerLat);

      for (let i = 0; i < ring.length - 1; i++) {
        const [x1, y1] = ring[i];
        const [x2, y2] = ring[i + 1];
        area += (x1 * mPerDegLon * y2 * mPerDegLat) - (y1 * mPerDegLat * x2 * mPerDegLon);
      }
      return Math.abs(area / 2);
    };

    minesData.forEach(f => {
      const p = f.properties || {};
      
      // --- Area & Size ---
      let area = Number(p.TBTYMJ || p.TBTYMJ_1 || 0);
      if ((!area || area <= 0) && f.geometry) {
         if (f.geometry.type === 'Polygon') {
            area = calculateArea(f.geometry.coordinates);
         } else if (f.geometry.type === 'MultiPolygon') {
            f.geometry.coordinates.forEach(poly => { area += calculateArea(poly); });
         }
      }
      totalArea += area;

      // Categorize by size (1 km2 = 1,000,000 m2)
      if (area < 1000000) {
        smallMines++;
      } else if (area >= 1000000 && area <= 2000000) {
        mediumMines++;
      } else {
        largeMines++;
      }

      // --- Mining Method (KCFS) ---
      const method = String(p.KCFS || '未知').trim();
      miningMethodStats[method] = (miningMethodStats[method] || 0) + 1;      // --- Closing Year (GBND) ---
      let year = String(p.GBND || '未知').trim();
      const yearMatch = year.match(/20\d{2}/);
      if (yearMatch) year = `${yearMatch[0]}年`;
      closingYearStats[year] = (closingYearStats[year] || 0) + 1;

      // --- Restoration Status (HFZLQK) ---
      const statusRaw = String(p.HFZLQK || '').trim();
      let statusSimple = '未知';
      if (/(未治|未恢复|未治理)/.test(statusRaw)) {
        statusSimple = '未治理';
        untreatedCount++;
      } else if (/(已|治理|复垦|恢复)/.test(statusRaw)) {
        statusSimple = '已恢复治理';
        treatedCount++;
      } else {
        statusSimple = statusRaw || '未知';
        if (statusSimple !== '未知') untreatedCount++;
      }

      restorationStatusStats[statusSimple] = (restorationStatusStats[statusSimple] || 0) + 1;

      // --- Restoration Method (NXFFS) ---
      const restMethod = String(p.NXFFS || '未知').trim();
      restorationMethodStats[restMethod] = (restorationMethodStats[restMethod] || 0) + 1;

      // --- Damage Type (STWT) ---
      const damageType = String(p.STWT || '未知').trim();
      damageTypeStats[damageType] = (damageTypeStats[damageType] || 0) + 1;

      // --- Land Type After (NXFFX) ---
      getLandTypeList(p.NXFFX).forEach((landType) => {
        landTypeStats[landType] = (landTypeStats[landType] || 0) + 1;
      });
      const fid = p.FID_1;
      const changeArea = computeMineChangedArea(fid);
      if (Number.isFinite(changeArea.changed_area_km2)) {
        validMineCount++;
        totalChangedAreaKm2 += changeArea.changed_area_km2;
      } else {
        missingMineCount++;
      }
      mineChangeAreaList.push({
        fid: Number(fid),
        mine_name: p.mine_name || p.name || `Mine_${fid}`,
        changed_area_km2: changeArea.changed_area_km2,
        area_source: changeArea.area_source,
        has_inference_output: changeArea.has_inference_output,
        has_change_matrix: changeArea.has_change_matrix
      });
    });

    // Format Data for Frontend
    // 1. Mining Method (Top 5)
    const miningMethodList = Object.entries(miningMethodStats)
      .sort((a, b) => b[1] - a[1])
      .map(([name, value]) => ({ name, value }));

    // 2. Restoration Method (Top 5)
    const restorationMethodList = Object.entries(restorationMethodStats)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => ({ name, count }));

    // 3. Land Type (Top 5)
    const landTypeList = Object.entries(landTypeStats)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6) // Top 6
      .map(([name, value]) => ({ name, value }));
      
    // 4. Closing Year (Top 5)
    const closingYearList = Object.entries(closingYearStats)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([name, value]) => ({ name, value }));

    // 2. NDVI Statistics (Keep existing logic)
    let totalNdvi = 0;
    let totalTrend = 0;
    let ndviCount = 0;
    let trendCount = 0;

    Object.values(ndviData).forEach(records => {
      if (!records.length) return;
      const vals = records.map(r => r.value);
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      totalNdvi += mean;
      ndviCount++;
      if (records.length >= 2) {
          const years = records.map(r => r.year);
          const n = records.length;
          const sumX = years.reduce((a, b) => a + b, 0);
          const sumY = vals.reduce((a, b) => a + b, 0);
          const sumXY = years.reduce((acc, x, i) => acc + x * vals[i], 0);
          const sumXX = years.reduce((acc, x) => acc + x * x, 0);
          const denom = n * sumXX - sumX * sumX;
          const slope = denom !== 0 ? (n * sumXY - sumX * sumY) / denom : 0;
          totalTrend += slope;
          trendCount++;
      }
    });

    const avgNdvi = ndviCount > 0 ? (totalNdvi / ndviCount) : 0;
    const avgTrend = trendCount > 0 ? (totalTrend / trendCount) : 0;

    res.json({
      mineTotal: minesData.length,
      mineAreaTotal: totalArea,
      treatedCount,
      untreatedCount,
      areaStats: {
        small: smallMines,   // < 1km2
        medium: mediumMines, // 1-2km2
        large: largeMines    // > 2km2
      },
      // New Stats
      miningMethodList,
      restorationMethodList,
      landTypeList,
      closingYearList,
      // Keep NDVI for compatibility if needed, though we might replace UI
      ndviStats: {
        mean: Number(avgNdvi.toFixed(3)),
        trend: Number(avgTrend.toFixed(5))
      },
      changeAreaStats: {
        total_changed_km2: Number(totalChangedAreaKm2.toFixed(6)),
        valid_mine_count: validMineCount,
        missing_mine_count: missingMineCount,
        coverage_ratio: minesData.length > 0 ? Number((validMineCount / minesData.length).toFixed(4)) : 0
      },
      mineChangeAreaList
    });
  });

// Get all mines as GeoJSON
app.get('/api/geojson', authGuard, (req, res) => {
  res.json({
    type: 'FeatureCollection',
    features: minesData
  });
});

// Search mines
app.use('/api/mines', authGuard);
app.get('/api/mines/search', (req, res) => {
  const q = req.query.q;
  if (!q) return res.status(400).json({ error: 'Missing query parameter q' });
  
  const qStr = String(q).toLowerCase();
  const fid = parseInt(q, 10);
  
  let found = null;
  
  // Try exact FID match first
  if (!isNaN(fid)) {
    found = minesData.find(f => f.properties && f.properties.FID_1 === fid);
  }
  
  // If not found, try name match
  if (!found) {
    found = minesData.find(f => {
      if (!f.properties) return false;
      const name = f.properties.mine_name || f.properties.name || '';
      return name.toLowerCase().includes(qStr);
    });
  }
  
  if (!found) return res.status(404).json({ error: 'Mine not found' });
  
  res.json(found);
});

// Get Indices Data (NDVI, NDBI, NDWI)
app.get('/api/mines/indices', (req, res) => {
  const { fid } = req.query;
  if (!fid) return res.status(400).json({ error: 'Missing FID parameter' });
  
  const fidNum = Number(fid);

  res.json(buildIndicesPayload({
    fid: fidNum,
    sourceData: {
      ndvi: ndviData[fidNum] || [],
      ndbi: ndbiData[fidNum] || [],
      ndwi: ndwiData[fidNum] || [],
      ndsi: ndsiData[fidNum] || [],
    },
    availability: indexAvailability,
  }));
});

// Get Change Matrix Data
app.get('/api/mines/change-matrix', (req, res) => {
  const { fid } = req.query;
  if (!fid) return res.status(400).json({ error: 'Missing FID parameter' });

  const csvPath = path.resolve(process.cwd(), 'change_matrix_outputs', fid, 'change_matrix_percent_rownorm.csv');
  const dirPath = path.resolve(process.cwd(), 'change_matrix_outputs', fid);
  
  if (fs.existsSync(csvPath)) {
    try {
      const content = fs.readFileSync(csvPath, 'utf-8');
      // Simple CSV parsing for this specific format
      const lines = content.trim().split('\n');
      if (lines.length === 0) return res.status(404).json({ error: 'Empty matrix' });

      const headers = lines[0].split(',').map(h => h.trim().replace(/^\uFEFF/, '')); // Remove BOM if present
      const matrix = [];
      
      for (let i = 1; i < lines.length; i++) {
        const parts = lines[i].split(',');
        const rowLabel = parts[0].trim();
        const rowValues = parts.slice(1).map(v => Number(v));
        matrix.push({ label: rowLabel, values: rowValues });
      }

      const newImageName = `${fid}_new.png`;
      const oldImageName = `${fid}_old.png`;
      const newImage = fs.existsSync(path.join(dirPath, newImageName)) ? buildChangeMatrixAssetPath(fid, newImageName) : null;
      const oldImage = fs.existsSync(path.join(dirPath, oldImageName)) ? buildChangeMatrixAssetPath(fid, oldImageName) : null;

      const changeArea = computeMineChangedArea(fid);
      const matrixSource = (changeArea.area_source === 'pixel_resolution' || changeArea.area_source === 'pixel_resolution_assumed_1m')
        ? 'pixel_resolution'
        : 'historical_output';

      res.json({
        fid: Number(fid),
        has_change_matrix: true,
        data_source: matrixSource,
        changed_area_km2: changeArea.changed_area_km2,
        area_source: changeArea.area_source,
        has_inference_output: changeArea.has_inference_output,
        headers: headers.slice(1), // First column is empty or row label header
        matrix: matrix,
        images: {
          new: newImage,
          old: oldImage
        }
      });
    } catch (e) {
      console.error(`Failed to read change matrix for FID ${fid}:`, e);
      res.status(500).json({ error: 'Failed to read matrix file' });
    }
  } else {
    const changeArea = computeMineChangedArea(fid);
    res.status(404).json({
      error: 'Change matrix not found for this FID',
      fid: Number(fid),
      has_change_matrix: false,
      data_source: 'none',
      changed_area_km2: changeArea.changed_area_km2,
      area_source: changeArea.area_source,
      has_inference_output: changeArea.has_inference_output
    });
  }
});

app.get('/api/mines/change-area-summary', (req, res) => {
  const list = minesData.map((f) => {
    const p = f.properties || {};
    const fid = p.FID_1;
    const area = computeMineChangedArea(fid);
    return {
      fid: Number(fid),
      mine_name: p.mine_name || p.name || `Mine_${fid}`,
      changed_area_km2: area.changed_area_km2,
      area_source: area.area_source,
      has_inference_output: area.has_inference_output,
      has_change_matrix: area.has_change_matrix
    };
  });
  res.json({
    mine_total: list.length,
    valid_mine_count: list.filter((x) => Number.isFinite(x.changed_area_km2)).length,
    list
  });
});

app.get('/api/mines/trend-report', (req, res) => {
  const className = String(req.query.class_name || 'bareground').trim();
  const direction = String(req.query.direction || 'all').trim();
  const report = buildTrendReport({
    outputRoot: defaultOutputRoot,
    minesData,
    className,
    direction
  });
  res.json(report);
});

app.use('/api/kml', authGuard);
app.post('/api/kml/upload', (req, res) => {
  try {
    const result = saveKmlUpload({
      uploadRoot: defaultKmlUploadRoot,
      filename: req.body?.filename,
      content: req.body?.content
    });
    res.json(result);
  } catch (err) {
    res.status(400).json({
      error: err?.message || 'KML 上传失败',
      next: '请确认文件扩展名为 .kml，且文件内容非空'
    });
  }
});

// Get NDVI data and trend (Legacy/Specific)
app.get('/api/mines/ndvi', (req, res) => {
  const { fid } = req.query;
  if (!fid) return res.status(400).json({ error: 'Missing FID parameter' });
  
  const fidNum = Number(fid);
  const data = ndviData[fidNum];
  
  if (!data || data.length === 0) {
    return res.status(404).json({ error: 'No NDVI data for this FID' });
  }
  
  const stats = calculateStats(data);

  res.json({
    fid: fidNum,
    ndvi_data: data, // Keep naming for compatibility if needed, but data has .value now
    ndvi_mean: stats.mean,
    ndvi_trend: stats.trend,
    mk_trend: stats.mk_trend
  });
});

// KML ROI inference for one image that may contain multiple mines.
app.use('/api/inference', authGuard);
app.post('/api/inference/kml-roi', async (req, res) => {
  try {
    return relayBackendResponse(
      res,
      await inferenceBackend.createJob(req.body || {}, req.headers.cookie || ''),
    );
  } catch (err) {
    return res.status(502).json({ success: false, code: 1, msg: err?.message || String(err) });
  }
});

app.get('/api/inference/jobs/:jobId', async (req, res) => {
  relayBackendResponse(res, await inferenceBackend.getJob(req.params.jobId, req.headers.cookie || ''));
});

app.post('/api/inference/jobs/:jobId/cancel', async (req, res) => {
  relayBackendResponse(res, await inferenceBackend.cancelJob(req.params.jobId, req.headers.cookie || ''));
});

app.get('/api/inference/capabilities', async (req, res) => {
  relayBackendResponse(res, await inferenceBackend.getCapabilities(req.headers.cookie || ''));
});
app.listen(port, () => {
  console.log(`Server running at http://localhost:${port}`);
  console.log('Mode: Local File System (No Database)');
});
