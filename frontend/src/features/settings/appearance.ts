import { apiFetch } from "../../api/client";

export interface AppearanceSettings {
  backgroundColor: string;
  textColor: string;
  accentColor: string;
}

export const DEFAULT_APPEARANCE_SETTINGS: AppearanceSettings = {
  backgroundColor: "#0b0d10",
  textColor: "#e7ebf0",
  accentColor: "#3b82f6",
};

const STORAGE_KEY = "server-control.appearance";
const HEX_COLOR_RE = /^#[0-9a-f]{6}$/i;

interface AppearanceResponse {
  settings: AppearanceSettings;
  persisted: boolean;
}

export function readAppearanceSettings(): AppearanceSettings {
  return readStoredAppearanceSettings() ?? DEFAULT_APPEARANCE_SETTINGS;
}

export function hasStoredAppearanceSettings(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(STORAGE_KEY) !== null;
}

export function readStoredAppearanceSettings(): AppearanceSettings | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isAppearanceRecord(parsed)) return null;

    return {
      backgroundColor: normalizeHexColor(
        parsed.backgroundColor,
        DEFAULT_APPEARANCE_SETTINGS.backgroundColor,
      ),
      textColor: normalizeHexColor(
        parsed.textColor,
        DEFAULT_APPEARANCE_SETTINGS.textColor,
      ),
      accentColor: normalizeHexColor(
        parsed.accentColor,
        DEFAULT_APPEARANCE_SETTINGS.accentColor,
      ),
    };
  } catch {
    return null;
  }
}

export async function fetchServerAppearanceSettings(): Promise<AppearanceResponse> {
  const response = await apiFetch<AppearanceResponse>("/api/settings/appearance");
  return {
    settings: normalizeAppearanceSettings(response.settings),
    persisted: response.persisted,
  };
}

export async function syncAppearanceSettingsFromServer(): Promise<AppearanceResponse> {
  const response = await fetchServerAppearanceSettings();
  if (response.persisted || !hasStoredAppearanceSettings()) {
    writeLocalAppearanceSettings(response.settings);
    applyAppearanceSettings(response.settings);
  }
  return response;
}

export async function saveAppearanceSettings(settings: AppearanceSettings) {
  const next = normalizeAppearanceSettings(settings);
  const response = await apiFetch<AppearanceResponse>("/api/settings/appearance", {
    method: "PUT",
    body: JSON.stringify({ settings: next }),
  });
  const saved = normalizeAppearanceSettings(response.settings);
  writeLocalAppearanceSettings(saved);
  applyAppearanceSettings(saved);
  return saved;
}

export async function resetAppearanceSettings() {
  const response = await apiFetch<AppearanceResponse>("/api/settings/appearance", {
    method: "DELETE",
  });
  const next = normalizeAppearanceSettings(response.settings);
  writeLocalAppearanceSettings(next);
  applyAppearanceSettings(next);
  return next;
}

function writeLocalAppearanceSettings(settings: AppearanceSettings) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(normalizeAppearanceSettings(settings)),
    );
  }
}

export function applyAppearanceSettings(settings: AppearanceSettings) {
  if (typeof document === "undefined") return;

  const root = document.documentElement;
  const variables = buildCssVariables(normalizeAppearanceSettings(settings));
  Object.entries(variables).forEach(([name, value]) => {
    root.style.setProperty(name, value);
  });
}

export function normalizeHexColor(value: string, fallback: string) {
  const trimmed = value.trim();
  if (HEX_COLOR_RE.test(trimmed)) return trimmed.toLowerCase();
  return fallback;
}

function normalizeAppearanceSettings(settings: AppearanceSettings): AppearanceSettings {
  return {
    backgroundColor: normalizeHexColor(
      settings.backgroundColor,
      DEFAULT_APPEARANCE_SETTINGS.backgroundColor,
    ),
    textColor: normalizeHexColor(
      settings.textColor,
      DEFAULT_APPEARANCE_SETTINGS.textColor,
    ),
    accentColor: normalizeHexColor(
      settings.accentColor,
      DEFAULT_APPEARANCE_SETTINGS.accentColor,
    ),
  };
}

function isAppearanceRecord(value: unknown): value is AppearanceSettings {
  return (
    typeof value === "object" &&
    value !== null &&
    "backgroundColor" in value &&
    "textColor" in value &&
    "accentColor" in value &&
    typeof (value as AppearanceSettings).backgroundColor === "string" &&
    typeof (value as AppearanceSettings).textColor === "string" &&
    typeof (value as AppearanceSettings).accentColor === "string"
  );
}

interface Rgb {
  r: number;
  g: number;
  b: number;
}

function buildCssVariables(settings: AppearanceSettings) {
  const background = hexToRgb(settings.backgroundColor);
  const text = hexToRgb(settings.textColor);
  const accent = hexToRgb(settings.accentColor);
  const surfaceTarget = luminance(background) > 0.45
    ? { r: 0, g: 0, b: 0 }
    : { r: 255, g: 255, b: 255 };
  const accentHover = luminance(accent) > 0.42
    ? mix(accent, background, 0.18)
    : mix(accent, text, 0.18);

  return {
    "--bg-canvas": settings.backgroundColor,
    "--bg-surface": rgbToHex(mix(background, surfaceTarget, 0.055)),
    "--bg-surface-2": rgbToHex(mix(background, surfaceTarget, 0.095)),
    "--bg-surface-3": rgbToHex(mix(background, surfaceTarget, 0.135)),
    "--bg-row-hover": rgbToHex(mix(background, surfaceTarget, 0.135)),
    "--bg-input": rgbToHex(mix(background, surfaceTarget, 0.075)),
    "--border-subtle": rgbToHex(mix(background, surfaceTarget, 0.16)),
    "--border-default": rgbToHex(mix(background, surfaceTarget, 0.23)),
    "--border-strong": rgbToHex(mix(background, surfaceTarget, 0.32)),
    "--text-primary": settings.textColor,
    "--text-secondary": rgbToHex(mix(text, background, 0.33)),
    "--text-muted": rgbToHex(mix(text, background, 0.55)),
    "--text-inverse": settings.backgroundColor,
    "--accent": settings.accentColor,
    "--accent-hover": rgbToHex(accentHover),
    "--accent-on": luminance(accent) > 0.5 ? "#0b0d10" : "#ffffff",
    "--accent-soft": `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.16)`,
  };
}

function hexToRgb(hex: string): Rgb {
  const raw = hex.replace("#", "");
  return {
    r: Number.parseInt(raw.slice(0, 2), 16),
    g: Number.parseInt(raw.slice(2, 4), 16),
    b: Number.parseInt(raw.slice(4, 6), 16),
  };
}

function rgbToHex(rgb: Rgb) {
  return `#${[rgb.r, rgb.g, rgb.b]
    .map((value) => clampChannel(value).toString(16).padStart(2, "0"))
    .join("")}`;
}

function mix(base: Rgb, overlay: Rgb, overlayWeight: number): Rgb {
  const baseWeight = 1 - overlayWeight;
  return {
    r: base.r * baseWeight + overlay.r * overlayWeight,
    g: base.g * baseWeight + overlay.g * overlayWeight,
    b: base.b * baseWeight + overlay.b * overlayWeight,
  };
}

function clampChannel(value: number) {
  return Math.max(0, Math.min(255, Math.round(value)));
}

function luminance(rgb: Rgb) {
  const [r, g, b] = [rgb.r, rgb.g, rgb.b].map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.03928
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}


// ----------------------------------------------------------------------
// Color presets (v1.2.2+)
// ----------------------------------------------------------------------

export interface AppearancePreset {
  name: string;
  settings: AppearanceSettings;
}

const PRESETS_STORAGE_KEY = "server-control.appearance.presets";

export function readAppearancePresets(): AppearancePreset[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(PRESETS_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const out: AppearancePreset[] = [];
    for (const item of parsed) {
      if (
        typeof item !== "object" ||
        item === null ||
        typeof (item as AppearancePreset).name !== "string" ||
        !isAppearanceRecord((item as AppearancePreset).settings)
      ) {
        continue;
      }
      const preset = item as AppearancePreset;
      if (!preset.name.trim()) continue;
      out.push({
        name: preset.name.trim(),
        settings: {
          backgroundColor: normalizeHexColor(
            preset.settings.backgroundColor,
            DEFAULT_APPEARANCE_SETTINGS.backgroundColor,
          ),
          textColor: normalizeHexColor(
            preset.settings.textColor,
            DEFAULT_APPEARANCE_SETTINGS.textColor,
          ),
          accentColor: normalizeHexColor(
            preset.settings.accentColor,
            DEFAULT_APPEARANCE_SETTINGS.accentColor,
          ),
        },
      });
    }
    return out;
  } catch {
    return [];
  }
}

function writeAppearancePresets(presets: AppearancePreset[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    PRESETS_STORAGE_KEY,
    JSON.stringify(presets),
  );
}

/** 같은 이름이 있으면 덮어쓰고, 없으면 끝에 추가한다. 정규화된 결과 리스트를 반환. */
export function upsertAppearancePreset(
  name: string,
  settings: AppearanceSettings,
): AppearancePreset[] {
  const trimmed = name.trim();
  if (!trimmed) return readAppearancePresets();
  const current = readAppearancePresets();
  const idx = current.findIndex((p) => p.name === trimmed);
  const next: AppearancePreset = {
    name: trimmed,
    settings: {
      backgroundColor: normalizeHexColor(
        settings.backgroundColor,
        DEFAULT_APPEARANCE_SETTINGS.backgroundColor,
      ),
      textColor: normalizeHexColor(
        settings.textColor,
        DEFAULT_APPEARANCE_SETTINGS.textColor,
      ),
      accentColor: normalizeHexColor(
        settings.accentColor,
        DEFAULT_APPEARANCE_SETTINGS.accentColor,
      ),
    },
  };
  const updated = idx >= 0 ? current.map((p, i) => (i === idx ? next : p)) : [...current, next];
  writeAppearancePresets(updated);
  return updated;
}

export function deleteAppearancePreset(name: string): AppearancePreset[] {
  const trimmed = name.trim();
  const updated = readAppearancePresets().filter((p) => p.name !== trimmed);
  writeAppearancePresets(updated);
  return updated;
}

export function hasAppearancePreset(name: string): boolean {
  const trimmed = name.trim();
  return readAppearancePresets().some((p) => p.name === trimmed);
}
