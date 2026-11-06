import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { DEFAULT_SETTINGS, SETTINGS_KEY } from "../hooks/useSettings";
import { MuteButton, SettingsMenu } from "./SettingsMenu";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.tableTheme;
});

const stored = () => JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}");

describe("SettingsMenu", () => {
  it("offers sound, volume and theme controls", async () => {
    const onChange = vi.fn();
    const onPreview = vi.fn();
    render(<SettingsMenu settings={DEFAULT_SETTINGS} onChange={onChange} onPreviewSound={onPreview} />);
    await userEvent.click(screen.getByRole("button", { name: "Settings" }));

    await userEvent.click(screen.getByRole("checkbox", { name: "Sound effects" }));
    expect(onChange).toHaveBeenLastCalledWith({ sound: false });

    const volume = screen.getByRole("slider", { name: "Volume" });
    expect(volume).toHaveValue("60");
    fireEvent.change(volume, { target: { value: "65" } });
    fireEvent.keyUp(volume, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith({ volume: 0.65 });
    expect(onPreview).toHaveBeenCalled();

    const themes = within(screen.getByRole("group", { name: "Table theme" })).getAllByRole("button");
    expect(themes.map((t) => t.textContent)).toEqual(["Classic green", "Midnight blue", "Burgundy"]);
    expect(screen.getByRole("button", { name: "Classic green" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Burgundy" }));
    expect(onChange).toHaveBeenLastCalledWith({ theme: "burgundy" });
  });

  it("disables the volume slider while sound is off", async () => {
    render(<SettingsMenu settings={{ ...DEFAULT_SETTINGS, sound: false }} onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("slider", { name: "Volume" })).toBeDisabled();
  });

  it("is a toggle with a fixed name, pressed while muted", () => {
    const { rerender } = render(<MuteButton muted={false} onToggle={() => {}} />);
    expect(screen.getByRole("button", { name: "Mute sound effects" })).toHaveAttribute("aria-pressed", "false");
    rerender(<MuteButton muted onToggle={() => {}} />);
    expect(screen.getByRole("button", { name: "Mute sound effects" })).toHaveAttribute("aria-pressed", "true");
  });

  it("closes on Escape and returns focus to the settings button", async () => {
    render(<SettingsMenu settings={DEFAULT_SETTINGS} onChange={() => {}} />);
    const gear = screen.getByRole("button", { name: "Settings" });
    await userEvent.click(gear);
    await userEvent.click(screen.getByRole("button", { name: "fast" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Table theme" })).not.toBeInTheDocument();
    expect(gear).toHaveFocus();
  });
});

describe("saved preferences", () => {
  it("persists mute, volume, theme and speed across a reload", async () => {
    const { unmount } = render(<App />);
    expect(document.documentElement.dataset.tableTheme).toBe("classic");

    await userEvent.click(screen.getByRole("button", { name: "Mute sound effects" }));
    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    await userEvent.click(screen.getByRole("button", { name: "Midnight blue" }));
    await userEvent.click(screen.getByRole("button", { name: "fast" }));
    expect(document.documentElement.dataset.tableTheme).toBe("midnight");
    expect(stored()).toMatchObject({ sound: false, theme: "midnight", speed: "fast" });
    unmount();

    // "Reload": a fresh App reads the saved settings.
    render(<App />);
    expect(document.documentElement.dataset.tableTheme).toBe("midnight");
    expect(screen.getByRole("button", { name: "Mute sound effects" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("button", { name: "fast" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Midnight blue" })).toHaveAttribute("aria-pressed", "true");
  });

  it("unmuting at zero volume restores an audible level", async () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ volume: 0 }));
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Mute sound effects" }));
    expect(stored()).toMatchObject({ sound: true, volume: 0.6 });
  });

  it("recovers from corrupt saved settings", () => {
    localStorage.setItem(SETTINGS_KEY, "{not json");
    render(<App />);
    expect(document.documentElement.dataset.tableTheme).toBe("classic");
    expect(screen.getByRole("button", { name: "Mute sound effects" })).toHaveAttribute("aria-pressed", "false");
  });
});
