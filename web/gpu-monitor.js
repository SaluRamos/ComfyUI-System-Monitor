import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const PREFIX = "GPUMonitor";
let panel, list, status, toggle, timer;
let busy = false;
let collapsed = false;

function setting(name, fallback) {
    return app.ui.settings.getSettingValue(`${PREFIX}.${name}`) ?? fallback;
}

function positionPanel() {
    if (!panel) return;
    const position = setting("Position", "bottom-left");
    panel.dataset.position = position;
    panel.hidden = !setting("Enabled", true);
}

function render(snapshot) {
    list.replaceChildren();
    const rows = snapshot.ram ? [{ ...snapshot.ram, name: "RAM do sistema", memory_type: "RAM" }, ...snapshot.gpus] : snapshot.gpus;
    for (const gpu of rows) {
        const row = document.createElement("div");
        row.className = "gpu-monitor-row";
        const name = document.createElement("div");
        name.className = "gpu-monitor-name";
        name.textContent = gpu.memory_type === "RAM" ? gpu.name : `GPU ${gpu.index} · ${gpu.name}`;
        name.title = name.textContent;
        const memory = document.createElement("div");
        memory.className = "gpu-monitor-memory";
        const valid = Number.isFinite(gpu.used_bytes) && Number.isFinite(gpu.total_bytes) && gpu.total_bytes > 0;
        const percent = valid ? Math.min(100, Math.max(0, gpu.used_bytes / gpu.total_bytes * 100)) : null;
        const gib = (bytes) => Number.isFinite(bytes) ? (bytes / 1024 ** 3).toFixed(2) : "—";
        memory.textContent = `${gpu.memory_type ?? "VRAM"}: ${gib(gpu.used_bytes)} / ${gib(gpu.total_bytes)} GiB${valid ? ` · ${percent.toFixed(0)}%` : " · indisponível"}`;
        const track = document.createElement("div");
        track.className = "gpu-monitor-track";
        const fill = document.createElement("div");
        fill.className = "gpu-monitor-fill";
        fill.style.width = `${percent ?? 0}%`;
        fill.dataset.level = percent >= 90 ? "high" : percent >= 75 ? "medium" : "normal";
        track.append(fill);
        row.append(name, memory, track);
        list.append(row);
    }
    status.textContent = snapshot.errors.join(" ");
    status.hidden = !status.textContent;
    panel.dataset.stale = "false";
}

async function refresh() {
    clearTimeout(timer);
    if (busy || document.hidden || !setting("Enabled", true)) return;
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 7000);
    try {
        const response = await api.fetchApi("/gpu-monitor/status", { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const snapshot = await response.json();
        if (!Array.isArray(snapshot.gpus) || !Array.isArray(snapshot.errors)) throw new Error("Resposta inválida");
        render(snapshot);
    } catch (error) {
        panel.dataset.stale = "true";
        status.hidden = false;
        status.textContent = "Sem conexão com o monitor. Aguardando nova leitura…";
    } finally {
        clearTimeout(timeout);
        busy = false;
        if (!document.hidden && setting("Enabled", true)) {
            timer = setTimeout(refresh, Number(setting("Interval", "2")) * 1000);
        }
    }
}

app.registerExtension({
    name: "ComfyUI.GPUMonitor",
    settings: [
        { id: `${PREFIX}.Enabled`, name: "Mostrar monitor de GPUs", type: "boolean", defaultValue: true,
          category: ["GPU Monitor", "Painel", "Visibilidade"],
          onChange() { positionPanel(); if (panel) refresh(); } },
        { id: `${PREFIX}.Position`, name: "Canto da tela", type: "combo", defaultValue: "bottom-left",
          category: ["GPU Monitor", "Painel", "Posição"],
          options: [{ text: "Inferior direito", value: "bottom-right" }, { text: "Inferior esquerdo", value: "bottom-left" },
                    { text: "Superior direito", value: "top-right" }, { text: "Superior esquerdo", value: "top-left" }],
          onChange: positionPanel },
        { id: `${PREFIX}.Interval`, name: "Atualizar a cada (segundos)", type: "combo", defaultValue: "2",
          category: ["GPU Monitor", "Painel", "Atualização"], options: ["1", "2", "5", "10"],
          onChange() { if (panel) refresh(); } },
    ],
    setup() {
        if (document.getElementById("gpu-monitor-panel")) return;
        const stylesheet = document.createElement("link");
        stylesheet.rel = "stylesheet";
        stylesheet.href = new URL("./gpu-monitor.css", import.meta.url).href;
        document.head.append(stylesheet);
        panel = document.createElement("section");
        panel.id = "gpu-monitor-panel";
        panel.setAttribute("aria-label", "Monitor de RAM e VRAM das GPUs");
        const header = document.createElement("div");
        header.className = "gpu-monitor-header";
        const title = document.createElement("strong");
        title.textContent = "System Monitor";
        toggle = document.createElement("button");
        toggle.type = "button";
        toggle.textContent = "−";
        toggle.title = "Recolher painel";
        toggle.setAttribute("aria-expanded", "true");
        toggle.addEventListener("click", () => {
            collapsed = !collapsed;
            list.hidden = collapsed;
            toggle.textContent = collapsed ? "+" : "−";
            toggle.title = collapsed ? "Expandir painel" : "Recolher painel";
            toggle.setAttribute("aria-expanded", String(!collapsed));
        });
        header.append(title, toggle);
        list = document.createElement("div");
        list.className = "gpu-monitor-list";
        status = document.createElement("div");
        status.className = "gpu-monitor-status";
        status.textContent = "Consultando GPUs…";
        panel.append(header, list, status);
        document.body.append(panel);
        for (const event of ["pointerdown", "pointerup", "wheel", "dblclick"]) {
            panel.addEventListener(event, (e) => e.stopPropagation());
        }
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) clearTimeout(timer);
            else refresh();
        });
        window.addEventListener("pagehide", () => clearTimeout(timer));
        positionPanel();
        refresh();
    },
});

