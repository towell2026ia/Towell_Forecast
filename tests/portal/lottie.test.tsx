import React from "react";
import { it, expect, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import ForecastAssistantLottie from "../../app/forecast-assistant-lottie";
const player = vi.hoisted(() => ({ loadAnimation: vi.fn(), addEventListener: vi.fn(), destroy: vi.fn() }));
vi.mock("lottie-web/build/player/lottie_light", () => ({ default: { loadAnimation: player.loadAnimation } }));

it("uses the original SVG loop/autoplay asset and handles failure and cleanup", async () => {
  player.loadAnimation.mockReturnValue({ addEventListener: player.addEventListener, destroy: player.destroy });
  const onError = vi.fn(), { unmount } = render(<ForecastAssistantLottie onError={onError}/>);
  await waitFor(() => expect(player.loadAnimation).toHaveBeenCalledOnce());
  expect(player.loadAnimation).toHaveBeenCalledWith(expect.objectContaining({ renderer: "svg", loop: true, autoplay: true, path: "/lottie/forecast-assistant.json" }));
  expect(player.addEventListener).toHaveBeenCalledWith("data_failed", onError);
  expect(player.addEventListener).toHaveBeenCalledWith("error", onError);
  unmount(); expect(player.destroy).toHaveBeenCalledOnce();
});
