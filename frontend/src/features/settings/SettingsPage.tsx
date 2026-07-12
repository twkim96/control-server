import { useEffect, useMemo, useState, type FormEvent } from "react";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Input } from "../../components/Input";
import { useToast } from "../../components/useToast";
import { reloadConfig } from "../../api/config";
import { describeError } from "../../utils/errors";
import { HexColorInput } from "./HexColorInput";
import {
  type AppearancePreset,
  type AppearanceSettings,
  deleteAppearancePreset,
  hasAppearancePreset,
  hasStoredAppearanceSettings,
  readAppearancePresets,
  readAppearanceSettings,
  resetAppearanceSettings,
  saveAppearanceSettings,
  syncAppearanceSettingsFromServer,
  upsertAppearancePreset,
} from "./appearance";
import classes from "./settings.module.css";
import componentClasses from "../../components/components.module.css";

type AppearanceKey = keyof AppearanceSettings;

const COLOR_FIELDS: Array<{
  key: AppearanceKey;
  label: string;
  description: string;
}> = [
  {
    key: "backgroundColor",
    label: "배경 색",
    description: "전체 화면과 상단 UI의 기본 배경",
  },
  {
    key: "textColor",
    label: "글자 색",
    description: "제목, 본문, 테이블 주요 텍스트",
  },
  {
    key: "accentColor",
    label: "포인트 컬러",
    description: "활성 탭, 버튼, 포커스 링",
  },
];

export function SettingsPage() {
  const toast = useToast();
  const [saved, setSaved] = useState(() => readAppearanceSettings());
  const [draft, setDraft] = useState<AppearanceSettings>(saved);
  const [presets, setPresets] = useState<AppearancePreset[]>(() =>
    readAppearancePresets(),
  );

  const [savePresetOpen, setSavePresetOpen] = useState(false);
  const [loadPresetOpen, setLoadPresetOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const dirty = useMemo(
    () =>
      draft.backgroundColor !== saved.backgroundColor ||
      draft.textColor !== saved.textColor ||
      draft.accentColor !== saved.accentColor,
    [draft, saved],
  );

  function updateColor(key: AppearanceKey, value: string) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  useEffect(() => {
    let alive = true;
    void syncAppearanceSettingsFromServer()
      .then((response) => {
        if (!alive) return;
        if (response.persisted || !hasStoredAppearanceSettings()) {
          setSaved(response.settings);
          setDraft(response.settings);
        }
      })
      .catch(() => {
        // 시작 fallback은 이미 적용되어 있으므로 Settings 화면에서는 조용히 둔다.
      });
    return () => {
      alive = false;
    };
  }, []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const next = await saveAppearanceSettings(draft);
      setSaved(next);
      setDraft(next);
      toast.push("화면 색상을 서버에 저장했습니다.", "success");
    } catch (err) {
      toast.push(describeError(err), "error");
    } finally {
      setSaving(false);
    }
  }

  async function onReset() {
    setSaving(true);
    try {
      const next = await resetAppearanceSettings();
      setSaved(next);
      setDraft(next);
      toast.push("서버 테마를 기본 색상으로 되돌렸습니다.", "success");
    } catch (err) {
      toast.push(describeError(err), "error");
    } finally {
      setSaving(false);
    }
  }

  function handlePresetSaved(name: string) {
    const next = upsertAppearancePreset(name, draft);
    setPresets(next);
    toast.push(`프리셋 "${name}"을 저장했습니다.`, "success");
  }

  function handlePresetApply(preset: AppearancePreset) {
    setDraft(preset.settings);
    toast.push(`프리셋 "${preset.name}"을 적용했습니다. 저장 버튼을 눌러주세요.`, "default");
  }

  function handlePresetDelete(name: string) {
    setPresets(deleteAppearancePreset(name));
  }

  const [reloading, setReloading] = useState(false);

  async function handleReload() {
    setReloading(true);
    try {
      await reloadConfig();
      toast.push("config를 다시 읽었습니다.", "success");
    } catch (err) {
      toast.push(describeError(err), "error");
    } finally {
      setReloading(false);
    }
  }

  return (
    <main className={classes.page}>
      <section className={classes.section}>
        <header className={classes.sectionHeader}>
          <div>
            <h2 className={classes.sectionTitle}>Settings</h2>
            <p className={classes.sectionSub}>화면 색상 설정</p>
          </div>
        </header>

        <div className={classes.presetBar}>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setSavePresetOpen(true)}
          >
            프리셋 저장
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setLoadPresetOpen(true)}
          >
            프리셋 불러오기 ({presets.length})
          </Button>
        </div>

        <form className={classes.panel} onSubmit={onSubmit}>
          <div className={classes.colorList}>
            {COLOR_FIELDS.map((field) => (
              <label key={field.key} className={classes.colorRow}>
                <span className={classes.colorText}>
                  <span className={classes.colorLabel}>{field.label}</span>
                  <span className={classes.colorDescription}>
                    {field.description}
                  </span>
                </span>
                <span className={classes.colorControl}>
                  <HexColorInput
                    value={draft[field.key]}
                    onChange={(value) => updateColor(field.key, value)}
                    ariaLabel={`${field.label} hex 값`}
                  />
                  <input
                    type="color"
                    value={draft[field.key]}
                    onChange={(event) => updateColor(field.key, event.target.value)}
                    className={classes.colorInput}
                    aria-label={field.label}
                  />
                </span>
              </label>
            ))}
          </div>

          <PreviewBar settings={draft} />

          <footer className={classes.actions}>
            <Button type="button" variant="ghost" onClick={onReset} disabled={saving}>
              기본값 복원
            </Button>
            <Button type="submit" variant="primary" disabled={!dirty || saving} loading={saving}>
              저장
            </Button>
          </footer>
        </form>
      </section>

      <section className={classes.section} style={{ marginTop: "var(--space-6)" }}>
        <header className={classes.sectionHeader}>
          <div>
            <h2 className={classes.sectionTitle}>서버 설정</h2>
            <p className={classes.sectionSub}>
              config.yml을 직접 편집한 뒤 다시 읽어들이려면 사용하세요. 컨트롤 서버
              프로세스 자체를 재시작하지 않습니다.
            </p>
          </div>
        </header>

        <div className={classes.panel} style={{ padding: "var(--space-4) var(--space-5)" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "var(--space-3)",
            }}
          >
            <div>
              <div style={{ fontSize: "var(--font-md)", fontWeight: 600 }}>
                config 리로드
              </div>
              <div
                style={{
                  fontSize: "var(--font-sm)",
                  color: "var(--text-muted)",
                  marginTop: 2,
                }}
              >
                services / actions / unmanaged_policy 등 config.yml의 변경 사항을 즉시 반영.
              </div>
            </div>
            <Button
              variant="primary"
              onClick={handleReload}
              loading={reloading}
            >
              리로드
            </Button>
          </div>
        </div>
      </section>

      <SavePresetModal
        open={savePresetOpen}
        onClose={() => setSavePresetOpen(false)}
        onConfirm={(name) => {
          handlePresetSaved(name);
          setSavePresetOpen(false);
        }}
      />

      <LoadPresetModal
        open={loadPresetOpen}
        presets={presets}
        onClose={() => setLoadPresetOpen(false)}
        onApply={(preset) => {
          handlePresetApply(preset);
          setLoadPresetOpen(false);
        }}
        onDelete={handlePresetDelete}
      />
    </main>
  );
}

interface PreviewBarProps {
  settings: AppearanceSettings;
}

function PreviewBar({ settings }: PreviewBarProps) {
  return (
    <div className={classes.preview} aria-hidden>
      <span
        className={classes.previewCanvas}
        style={{ backgroundColor: settings.backgroundColor }}
      />
      <span
        className={classes.previewText}
        style={{ backgroundColor: settings.textColor }}
      />
      <span
        className={classes.previewAccent}
        style={{ backgroundColor: settings.accentColor }}
      />
    </div>
  );
}

interface SavePresetModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: (name: string) => void;
}

function SavePresetModal({ open, onClose, onConfirm }: SavePresetModalProps) {
  const [name, setName] = useState("");
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);

  if (!open) return null;

  const handleSubmit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (hasAppearancePreset(trimmed)) {
      setConfirmOverwrite(true);
      return;
    }
    onConfirm(trimmed);
    setName("");
  };

  return (
    <>
      <div
        className={componentClasses.modalOverlay}
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div
          className={componentClasses.modalCard}
          role="dialog"
          aria-modal="true"
        >
          <div className={componentClasses.modalHeader}>프리셋 저장</div>
          <div className={componentClasses.modalBody}>
            <label
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-1)",
              }}
            >
              <span
                style={{
                  fontSize: "var(--font-sm)",
                  color: "var(--text-secondary)",
                }}
              >
                프리셋 이름
              </span>
              <Input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="예: dark-yellow"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
              />
            </label>
          </div>
          <div className={componentClasses.modalFooter}>
            <Button
              variant="ghost"
              onClick={() => {
                setName("");
                onClose();
              }}
            >
              취소
            </Button>
            <Button
              variant="primary"
              onClick={handleSubmit}
              disabled={!name.trim()}
            >
              저장
            </Button>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmOverwrite}
        title="프리셋 덮어쓰기"
        message={`"${name.trim()}" 이름의 프리셋이 이미 있습니다. 덮어쓸까요?`}
        confirmLabel="덮어쓰기"
        variant="danger"
        onConfirm={() => {
          onConfirm(name.trim());
          setName("");
          setConfirmOverwrite(false);
        }}
        onCancel={() => setConfirmOverwrite(false)}
      />
    </>
  );
}

interface LoadPresetModalProps {
  open: boolean;
  presets: AppearancePreset[];
  onClose: () => void;
  onApply: (preset: AppearancePreset) => void;
  onDelete: (name: string) => void;
}

function LoadPresetModal({
  open,
  presets,
  onClose,
  onApply,
  onDelete,
}: LoadPresetModalProps) {
  const [selectedName, setSelectedName] = useState<string | undefined>();

  if (!open) return null;

  const selected = presets.find((p) => p.name === selectedName);

  return (
    <div
      className={componentClasses.modalOverlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={componentClasses.modalCard}
        role="dialog"
        aria-modal="true"
        style={{ maxWidth: "520px", width: "92vw" }}
      >
        <div className={componentClasses.modalHeader}>프리셋 불러오기</div>
        <div
          className={componentClasses.modalBody}
          style={{ maxHeight: "60vh", overflow: "auto" }}
        >
          {presets.length === 0 ? (
            <div
              style={{
                color: "var(--text-muted)",
                fontSize: "var(--font-sm)",
                textAlign: "center",
                padding: "var(--space-6) 0",
              }}
            >
              저장된 프리셋이 없습니다.
            </div>
          ) : (
            <ul className={classes.presetList}>
              {presets.map((preset) => {
                const active = preset.name === selectedName;
                return (
                  <li key={preset.name}>
                    <button
                      type="button"
                      className={`${classes.presetItem} ${
                        active ? classes.presetItemActive : ""
                      }`}
                      onClick={() => setSelectedName(preset.name)}
                      onDoubleClick={() => onApply(preset)}
                    >
                      <PreviewBar settings={preset.settings} />
                      <span className={classes.presetName}>{preset.name}</span>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(preset.name);
                          if (active) setSelectedName(undefined);
                        }}
                      >
                        삭제
                      </Button>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className={componentClasses.modalFooter}>
          <Button variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button
            variant="primary"
            disabled={!selected}
            onClick={() => selected && onApply(selected)}
          >
            선택
          </Button>
        </div>
      </div>
    </div>
  );
}
