import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const checkOnly = process.argv.includes("--check");

function readJson(relativePath) {
  return JSON.parse(readFileSync(join(root, relativePath), "utf8"));
}

function fail(message) {
  throw new Error(`Progress data is invalid: ${message}`);
}

function escapeCell(value) {
  return String(value ?? "-")
    .replaceAll("|", "\\|")
    .replaceAll("\r", " ")
    .replaceAll("\n", " ");
}

function listCell(values) {
  return values?.length ? values.map((value) => `\`${escapeCell(value)}\``).join("、") : "-";
}

const config = readJson("progress/config.json");
const risksSource = readJson("progress/risks.json");
const debtsSource = readJson("progress/tech-debts.json");
const logsSource = readJson("progress/logs.json");
const taskDirectory = join(root, "progress", "tasks");
const taskFiles = readdirSync(taskDirectory)
  .filter((name) => name.endsWith(".json"))
  .sort();
const tasks = taskFiles.flatMap((name) => readJson(`progress/tasks/${name}`).tasks ?? []);
const risks = risksSource.risks ?? [];
const techDebts = debtsSource.techDebts ?? [];
const logs = logsSource.logs ?? [];

const statusIds = new Set(config.statuses.map((status) => status.id));
const phaseIds = new Set(config.phases.map((phase) => phase.id));
const taskIds = new Set();

for (const task of tasks) {
  if (!task.id || taskIds.has(task.id)) fail(`duplicate or missing task id: ${task.id}`);
  taskIds.add(task.id);
  if (!phaseIds.has(task.phaseId)) fail(`${task.id} references unknown phase ${task.phaseId}`);
  if (!statusIds.has(task.status)) fail(`${task.id} uses unknown status ${task.status}`);
  if (!task.title || !task.acceptance || !Array.isArray(task.verification)) {
    fail(`${task.id} needs title, acceptance, and verification`);
  }
}

for (const task of tasks) {
  for (const dependency of task.dependsOn ?? []) {
    if (!taskIds.has(dependency)) fail(`${task.id} depends on unknown task ${dependency}`);
  }
}

const riskIds = new Set();
for (const risk of risks) {
  if (!risk.id || riskIds.has(risk.id)) fail(`duplicate or missing risk id: ${risk.id}`);
  riskIds.add(risk.id);
  if (!taskIds.has(risk.ownerTaskId)) fail(`${risk.id} references unknown task ${risk.ownerTaskId}`);
}

for (const log of logs) {
  if (!taskIds.has(log.taskId)) fail(`log references unknown task ${log.taskId}`);
}

function phaseStatus(phaseTasks) {
  if (phaseTasks.length === 0) return "未开始";
  if (phaseTasks.every((task) => task.status === "已完成")) return "已完成";
  if (phaseTasks.some((task) => task.status === "进行中" || task.status === "已完成")) return "进行中";
  if (phaseTasks.some((task) => task.status === "阻塞")) return "阻塞";
  if (phaseTasks.every((task) => task.status === "暂缓")) return "暂缓";
  return "未开始";
}

const statusCounts = Object.fromEntries(config.statuses.map((status) => [status.id, 0]));
for (const task of tasks) statusCounts[task.status] += 1;

const lines = [
  "# 检索改造开发进度",
  "",
  "> 此文件由 `progress/` 下的结构化数据生成，请勿手工修改。",
  "",
  `- 数据日期：${config.updatedOn}`,
  `- 方案文档：\`${config.planDocument}\``,
  `- 任务总数：${tasks.length}`,
  `- 已完成：${statusCounts["已完成"] ?? 0}`,
  `- 进行中：${statusCounts["进行中"] ?? 0}`,
  `- 阻塞：${statusCounts["阻塞"] ?? 0}`,
  "",
  "## 当前焦点",
  "",
];

const activeTasks = tasks.filter((task) => task.status === "进行中" || task.status === "阻塞");
if (activeTasks.length === 0) {
  lines.push("当前没有进行中或阻塞任务。", "");
} else {
  for (const task of activeTasks) {
    lines.push(`- \`${task.id}\` ${task.title}（${task.status}）：${task.acceptance}`);
  }
  lines.push("");
}

lines.push(
  "## 阶段概览",
  "",
  "| 阶段 | 目标 | 状态 | 完成度 |",
  "| --- | --- | --- | --- |",
);

for (const phase of config.phases) {
  const phaseTasks = tasks.filter((task) => task.phaseId === phase.id);
  const completed = phaseTasks.filter((task) => task.status === "已完成").length;
  lines.push(
    `| \`${phase.id}\` ${escapeCell(phase.label)} | ${escapeCell(phase.goal)} | ${phaseStatus(phaseTasks)} | ${completed}/${phaseTasks.length} |`,
  );
}

lines.push("");

for (const phase of config.phases) {
  const phaseTasks = tasks
    .filter((task) => task.phaseId === phase.id)
    .sort((left, right) => left.order - right.order || left.id.localeCompare(right.id));
  lines.push(
    `## ${phase.id} ${phase.label}`,
    "",
    "| 任务 | 优先级 | 状态 | 依赖 | 验收 | 验证 |",
    "| --- | --- | --- | --- | --- | --- |",
  );
  for (const task of phaseTasks) {
    lines.push(
      `| \`${task.id}\` ${escapeCell(task.title)} | ${escapeCell(task.priority)} | ${escapeCell(task.status)} | ${listCell(task.dependsOn)} | ${escapeCell(task.acceptance)} | ${listCell(task.verification)} |`,
    );
  }
  lines.push("");
}

lines.push(
  "## 风险",
  "",
  "| 风险 | 严重度 | 状态 | 影响 | 缓解措施 | 归属任务 |",
  "| --- | --- | --- | --- | --- | --- |",
);

if (risks.length === 0) {
  lines.push("| - | - | - | 当前无登记风险 | - | - |");
} else {
  for (const risk of risks) {
    lines.push(
      `| \`${risk.id}\` ${escapeCell(risk.title)} | ${escapeCell(risk.severity)} | ${escapeCell(risk.status)} | ${escapeCell(risk.impact)} | ${escapeCell(risk.mitigation)} | \`${risk.ownerTaskId}\` |`,
    );
  }
}

lines.push(
  "",
  "## 技术债",
  "",
);

if (techDebts.length === 0) {
  lines.push("当前无登记技术债。", "");
} else {
  for (const debt of techDebts) {
    lines.push(`- \`${debt.id}\` ${debt.title}（${debt.status}）：${debt.plan}`);
  }
  lines.push("");
}

lines.push("## 最近日志", "");
for (const log of [...logs].sort((left, right) => right.date.localeCompare(left.date)).slice(0, 10)) {
  lines.push(
    `- ${log.date} \`${log.taskId}\`：${log.action}。结果：${log.result} 下一步：${log.next}`,
  );
}
const output = `${lines.join("\n")}\n`;
const outputPath = join(root, config.generatedFile);

if (checkOnly) {
  const current = readFileSync(outputPath, "utf8");
  if (current !== output) {
    console.error(`Progress document is stale: ${config.generatedFile}`);
    process.exitCode = 1;
  } else {
    console.log(`Progress document is current: ${config.generatedFile}`);
  }
} else {
  writeFileSync(outputPath, output, "utf8");
  console.log(`Generated ${config.generatedFile}`);
}
