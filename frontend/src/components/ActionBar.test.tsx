import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { legal } from "../test/fixtures";
import { ActionBar } from "./ActionBar";

function setup(overrides = {}, props = {}) {
  const onAction = vi.fn();
  render(
    <ActionBar
      legal={legal(overrides)}
      pot={15}
      currentBet={10}
      bigBlind={10}
      disabled={false}
      onAction={onAction}
      {...props}
    />,
  );
  return onAction;
}

describe("ActionBar", () => {
  it("offers fold, call and raise when facing a bet", async () => {
    const onAction = setup();
    await userEvent.click(screen.getByRole("button", { name: /Call 5/ }));
    expect(onAction).toHaveBeenLastCalledWith("call");
    await userEvent.click(screen.getByRole("button", { name: /^Fold/ }));
    expect(onAction).toHaveBeenLastCalledWith("fold");
    await userEvent.click(screen.getByRole("button", { name: /Raise to 20/ }));
    expect(onAction).toHaveBeenLastCalledWith("raise", 20);
  });

  it("shows check and disables fold when there is nothing to call", async () => {
    const onAction = setup({ can_check: true, can_call: false, can_fold: false, call_amount: 0 });
    expect(screen.getByRole("button", { name: /^Fold/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /^Check/ }));
    expect(onAction).toHaveBeenLastCalledWith("check");
  });

  it("uses bet wording when no bet has been made", () => {
    setup({ raise_type: "bet", can_check: true, can_call: false, call_amount: 0, min_raise_to: 10 });
    expect(screen.getByRole("button", { name: /^Bet 10/ })).toBeInTheDocument();
  });

  it("sends all_in when the max equals the player's stack", async () => {
    const onAction = setup();
    await userEvent.click(screen.getByRole("button", { name: "All-in" }));
    await userEvent.click(screen.getByRole("button", { name: /All-in 1,000/ }));
    expect(onAction).toHaveBeenLastCalledWith("all_in");
  });

  it("raises to the capped max when the opponent is shorter", async () => {
    const onAction = setup({ max_raise_to: 300, raise_is_all_in: false });
    await userEvent.click(screen.getByRole("button", { name: "Max" }));
    await userEvent.click(screen.getByRole("button", { name: /Raise to 300/ }));
    expect(onAction).toHaveBeenLastCalledWith("raise", 300);
  });

  it("clamps typed amounts into the legal range", async () => {
    const onAction = setup();
    const input = screen.getByLabelText("Raise amount in chips");
    await userEvent.clear(input);
    await userEvent.type(input, "5{Enter}");
    expect(input).toHaveValue(20);
    await userEvent.clear(input);
    await userEvent.type(input, "123{Enter}");
    await userEvent.click(screen.getByRole("button", { name: /Raise to 123/ }));
    expect(onAction).toHaveBeenLastCalledWith("raise", 123);
  });

  it("nudges by the big blind", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Adjust raise +10" }));
    expect(screen.getByLabelText("Raise amount in chips")).toHaveValue(30);
  });

  it("hides raise controls when raising is not allowed", () => {
    setup({ can_raise: false, min_raise_to: 0, max_raise_to: 0 });
    expect(screen.queryByLabelText("Raise amount")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Raise/ })).not.toBeInTheDocument();
  });

  it("supports keyboard shortcuts and ignores them when disabled", () => {
    const onAction = setup();
    fireEvent.keyDown(window, { key: "c" });
    expect(onAction).toHaveBeenLastCalledWith("call");
    fireEvent.keyDown(window, { key: "f" });
    expect(onAction).toHaveBeenLastCalledWith("fold");
  });

  it("does nothing when disabled", async () => {
    const onAction = setup({}, { disabled: true });
    fireEvent.keyDown(window, { key: "c" });
    expect(screen.getByRole("button", { name: /Call 5/ })).toBeDisabled();
    expect(onAction).not.toHaveBeenCalled();
  });
});
