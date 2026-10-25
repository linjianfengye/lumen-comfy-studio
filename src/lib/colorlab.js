// 移植自新安学院官网「色彩实验室」。色彩 Preset：每个预设 = 饱和度 + RGB 三条色调曲线（等距采样）
// 同一份数据同时驱动 SVG 滤镜（页面实时预览）和 .cube 3D LUT 导出，保证两边一致
export const PRESETS = [
  { id: "raw", name: "原片", en: "RAW", sat: 1, r: [0, 1], g: [0, 1], b: [0, 1], note: "未经调色的原始画面。" },
  { id: "dawn", name: "清晨", en: "DAWN", sat: 0.82, r: [0.06, 0.5, 0.95], g: [0.07, 0.53, 0.98], b: [0.13, 0.59, 1], note: "抬高暗部，整体偏冷，清晨通透的空气感。" },
  { id: "film", name: "胶片", en: "FILM", sat: 0.78, r: [0.1, 0.31, 0.58, 0.82, 0.96], g: [0.08, 0.27, 0.53, 0.77, 0.9], b: [0.1, 0.24, 0.45, 0.66, 0.8], note: "褪色的黑位和暖黄高光，老胶片的温度。" },
  { id: "dusk", name: "夜色", en: "DUSK", sat: 0.9, r: [0.02, 0.2, 0.56, 0.99], g: [0.06, 0.26, 0.55, 0.9], b: [0.15, 0.37, 0.55, 0.74], note: "青色阴影、橙色高光，电影感的蓝调时刻。" },
  { id: "summer", name: "盛夏", en: "SUMMER", sat: 1.38, r: [0, 0.29, 0.7, 1], g: [0, 0.27, 0.67, 0.98], b: [0, 0.22, 0.58, 0.92], note: "更高的饱和与对比，盛夏的浓烈色彩。" },
];

// 按强度 k 把曲线向恒等线插值
export function mixPreset(preset, k) {
  const mix = (table) => table.map((v, i) => {
    const id = i / (table.length - 1);
    return +(id + (v - id) * k).toFixed(4);
  });
  return { sat: +(1 + (preset.sat - 1) * k).toFixed(4), r: mix(preset.r), g: mix(preset.g), b: mix(preset.b) };
}

// 与 SVG feFuncX type="table" 相同的分段线性插值
function lookup(table, x) {
  const n = table.length - 1;
  const pos = Math.min(Math.max(x, 0), 1) * n;
  const i = Math.min(Math.floor(pos), n - 1);
  return table[i] + (table[i + 1] - table[i]) * (pos - i);
}

// 与 SVG feColorMatrix type="saturate" 相同的矩阵
function saturate(r, g, b, s) {
  const clamp = (v) => Math.min(Math.max(v, 0), 1);
  return [
    clamp((0.213 + 0.787 * s) * r + (0.715 - 0.715 * s) * g + (0.072 - 0.072 * s) * b),
    clamp((0.213 - 0.213 * s) * r + (0.715 + 0.285 * s) * g + (0.072 - 0.072 * s) * b),
    clamp((0.213 - 0.213 * s) * r + (0.715 - 0.715 * s) * g + (0.072 + 0.928 * s) * b),
  ];
}

export function buildCube(name, curves, size = 17) {
  const lines = [`TITLE "LUMEN ${name}"`, `# Lumen · 色彩对比`, `LUT_3D_SIZE ${size}`, "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"];
  const step = size - 1;
  for (let bi = 0; bi < size; bi += 1) {
    for (let gi = 0; gi < size; gi += 1) {
      for (let ri = 0; ri < size; ri += 1) {
        const [r, g, b] = saturate(ri / step, gi / step, bi / step, curves.sat);
        lines.push([lookup(curves.r, r), lookup(curves.g, g), lookup(curves.b, b)].map((v) => v.toFixed(5)).join(" "));
      }
    }
  }
  return lines.join("\n") + "\n";
}
