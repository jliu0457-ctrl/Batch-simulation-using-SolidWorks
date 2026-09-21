import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const here = path.dirname(fileURLToPath(import.meta.url));

const VARIABLES = [
  { order: 1, name: "c_mm", meaning: "轴向偏心距", unit: "mm", lower: 30.4, upper: 33.6,
    basis: "基准值 32 mm 的 -5%～+5%" },
  { order: 2, name: "e_mm", meaning: "径向偏心距", unit: "mm", lower: 3.515, upper: 3.885,
    basis: "基准值 3.7 mm 的 -5%～+5%" },
  { order: 3, name: "phi_deg", meaning: "偏心角", unit: "°", lower: 5, upper: 11,
    basis: "指定范围" },
  { order: 4, name: "alpha_deg", meaning: "全锥角", unit: "°", lower: 20, upper: 40,
    basis: "对应物理半锥角 10°～20°" },
  { order: 5, name: "Dmax_mm", meaning: "密封锥底圆直径", unit: "mm", lower: 179.9, upper: 193.9,
    basis: "指定范围" },
  { order: 6, name: "bm_mm", meaning: "密封片厚度", unit: "mm", lower: 7, upper: 8,
    basis: "指定范围" },
  { order: 7, name: "ds_mm", meaning: "阀杆直径", unit: "mm", lower: 37.152, upper: 49,
    basis: "指定范围" },
];

function parseArgs(argv) {
  const out = {
    samples: 3200,
    seed: 20260921,
    output: path.join(here, "outputs", "七变量拉丁超立方采样_3200组.xlsx"),
    previewDir: path.join(here, "_qa_lhs"),
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "--samples") out.samples = Number(argv[++i]);
    else if (key === "--seed") out.seed = Number(argv[++i]);
    else if (key === "--output") out.output = path.resolve(argv[++i]);
    else if (key === "--preview-dir") out.previewDir = path.resolve(argv[++i]);
    else throw new Error(`未知参数：${key}`);
  }
  if (!Number.isInteger(out.samples) || out.samples <= 1) throw new Error("samples 必须是大于 1 的整数");
  if (!Number.isInteger(out.seed)) throw new Error("seed 必须是整数");
  return out;
}

function mulberry32(seed) {
  let value = seed >>> 0;
  return function random() {
    value += 0x6D2B79F5;
    let t = value;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffledIndices(count, random) {
  const values = Array.from({ length: count }, (_, index) => index);
  for (let i = count - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [values[i], values[j]] = [values[j], values[i]];
  }
  return values;
}

function latinHypercube(sampleCount, variables, seed) {
  const random = mulberry32(seed);
  const normalized = Array.from({ length: sampleCount }, () => Array(variables.length));
  for (let column = 0; column < variables.length; column += 1) {
    const strata = shuffledIndices(sampleCount, random);
    for (let row = 0; row < sampleCount; row += 1) {
      normalized[row][column] = (strata[row] + random()) / sampleCount;
    }
  }
  return normalized.map((sample) => sample.map((unitValue, column) => {
    const variable = variables[column];
    return variable.lower + unitValue * (variable.upper - variable.lower);
  }));
}

function verifyLhs(samples, variables) {
  const count = samples.length;
  if (count === 0 || samples.some((row) => row.length !== variables.length)) {
    throw new Error("采样矩阵维度错误");
  }
  return variables.map((variable, column) => {
    const seen = new Set();
    let minimum = Infinity;
    let maximum = -Infinity;
    let total = 0;
    for (const row of samples) {
      const value = row[column];
      if (!(value >= variable.lower && value < variable.upper)) {
        throw new Error(`${variable.name} 超界：${value}`);
      }
      const normalized = (value - variable.lower) / (variable.upper - variable.lower);
      seen.add(Math.min(count - 1, Math.floor(normalized * count)));
      minimum = Math.min(minimum, value);
      maximum = Math.max(maximum, value);
      total += value;
    }
    if (seen.size !== count) {
      throw new Error(`${variable.name} 只覆盖 ${seen.size}/${count} 个分层，不是合法 LHS`);
    }
    return { name: variable.name, strata: seen.size, minimum, maximum, mean: total / count };
  });
}

function applyHeaderStyle(range) {
  range.format = {
    fill: "#FFFFFF",
    font: { name: "Arial", size: 10, bold: true, color: "#000000" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: false,
    borders: { preset: "all", style: "thin", color: "#D9D9D9" },
  };
}

async function buildWorkbook(samples, variables, settings, verification) {
  const workbook = Workbook.create();

  const dataSheet = workbook.worksheets.add("LHS样本");
  dataSheet.showGridLines = false;
  const headers = ["样本序号", ...variables.map((v) => v.name)];
  const rows = samples.map((values, index) => [index + 1, ...values]);
  dataSheet.getRangeByIndexes(0, 0, rows.length + 1, headers.length).values = [headers, ...rows];
  const table = dataSheet.tables.add(`A1:H${rows.length + 1}`, true, "LhsSamplesTable");
  table.style = "TableStyleLight1";
  table.showBandedRows = false;
  table.showBandedColumns = false;
  table.showFilterButton = true;
  dataSheet.getRange(`A1:H${rows.length + 1}`).format.fill = "#FFFFFF";
  dataSheet.getRange(`A1:H${rows.length + 1}`).format.font = {
    name: "Arial", size: 10, color: "#000000",
  };
  applyHeaderStyle(dataSheet.getRange(`A1:H1`));
  dataSheet.getRange(`A2:A${rows.length + 1}`).format.numberFormat = "0";
  dataSheet.getRange(`B2:H${rows.length + 1}`).format.numberFormat = "0.000000";
  dataSheet.getRange(`A1:H${rows.length + 1}`).format.verticalAlignment = "center";
  dataSheet.getRange(`A2:H${rows.length + 1}`).format.borders = {
    bottom: { style: "hair", color: "#E7E6E6" },
  };
  dataSheet.getRange("A:A").format.columnWidth = 12;
  dataSheet.getRange("B:H").format.columnWidth = 16;
  dataSheet.getRange("A1:H1").format.rowHeight = 24;
  dataSheet.freezePanes.freezeRows(1);
  dataSheet.freezePanes.freezeColumns(1);

  const boundsSheet = workbook.worksheets.add("参数范围");
  boundsSheet.showGridLines = false;
  const boundsHeaders = ["变量序号", "变量名", "几何含义", "单位", "下界", "上界", "取值依据"];
  const boundsRows = variables.map((v) => [v.order, v.name, v.meaning, v.unit, v.lower, v.upper, v.basis]);
  boundsSheet.getRange("A1:G8").values = [boundsHeaders, ...boundsRows];
  const boundsTable = boundsSheet.tables.add("A1:G8", true, "VariableBoundsTable");
  boundsTable.style = "TableStyleLight1";
  boundsTable.showBandedRows = false;
  boundsTable.showBandedColumns = false;
  boundsSheet.getRange("A1:G8").format.fill = "#FFFFFF";
  applyHeaderStyle(boundsSheet.getRange("A1:G1"));
  boundsSheet.getRange("A2:G8").format.font = { name: "Arial", size: 10, color: "#000000" };
  boundsSheet.getRange("A1:G8").format.verticalAlignment = "center";
  boundsSheet.getRange("A2:G8").format.borders = { preset: "all", style: "thin", color: "#D9D9D9" };
  boundsSheet.getRange("E2:F8").format.numberFormat = "0.000000";
  boundsSheet.getRange("A:A").format.columnWidth = 12;
  boundsSheet.getRange("B:B").format.columnWidth = 16;
  boundsSheet.getRange("C:C").format.columnWidth = 22;
  boundsSheet.getRange("D:D").format.columnWidth = 10;
  boundsSheet.getRange("E:F").format.columnWidth = 14;
  boundsSheet.getRange("G:G").format.columnWidth = 34;
  boundsSheet.getRange("A1:G1").format.rowHeight = 24;
  boundsSheet.freezePanes.freezeRows(1);

  boundsSheet.getRange("I1:J4").values = [
    ["采样设置", "值"],
    ["样本数", settings.samples],
    ["随机种子", settings.seed],
    ["方法", "随机拉丁超立方"],
  ];
  applyHeaderStyle(boundsSheet.getRange("I1:J1"));
  boundsSheet.getRange("I2:J4").format = {
    fill: "#FFFFFF",
    font: { name: "Arial", size: 10, color: "#000000" },
    borders: { preset: "all", style: "thin", color: "#D9D9D9" },
    verticalAlignment: "center",
  };
  boundsSheet.getRange("I:I").format.columnWidth = 16;
  boundsSheet.getRange("J:J").format.columnWidth = 20;

  workbook.recalculate();

  const previewRows = Math.min(settings.samples + 1, 35);
  await fs.mkdir(settings.previewDir, { recursive: true });
  const dataPreview = await workbook.render({
    sheetName: "LHS样本", range: `A1:H${previewRows}`, scale: 1.5, format: "png",
  });
  await fs.writeFile(path.join(settings.previewDir, "LHS样本.png"),
    new Uint8Array(await dataPreview.arrayBuffer()));
  const boundsPreview = await workbook.render({
    sheetName: "参数范围", range: "A1:J8", scale: 1.5, format: "png",
  });
  await fs.writeFile(path.join(settings.previewDir, "参数范围.png"),
    new Uint8Array(await boundsPreview.arrayBuffer()));

  await fs.mkdir(path.dirname(settings.output), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(settings.output);

  const dataInspection = await workbook.inspect({
    kind: "table", range: "LHS样本!A1:H8", include: "values,formulas",
    tableMaxRows: 8, tableMaxCols: 8,
  });
  const boundsInspection = await workbook.inspect({
    kind: "table", range: "参数范围!A1:J8", include: "values,formulas",
    tableMaxRows: 10, tableMaxCols: 10,
  });
  const errorScan = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 50 },
    summary: "final formula error scan",
  });
  return {
    output: settings.output,
    previewDir: settings.previewDir,
    verification,
    inspections: {
      data: dataInspection.ndjson,
      bounds: boundsInspection.ndjson,
      errors: errorScan.ndjson,
    },
  };
}

const settings = parseArgs(process.argv.slice(2));
const samples = latinHypercube(settings.samples, VARIABLES, settings.seed);
const verification = verifyLhs(samples, VARIABLES);
const result = await buildWorkbook(samples, VARIABLES, settings, verification);
console.log(JSON.stringify(result, null, 2));
