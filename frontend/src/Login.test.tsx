// REQ-1.1 / REQ-4.4: login screen with Google sign-in and generic error display.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Login from "./Login";

describe("Login", () => {
  it("links 'Sign in with Google' to the backend login route (REQ-1.1)", () => {
    render(<Login error={null} />);
    const link = screen.getByRole("link", { name: /sign in with google/i });
    expect(link).toHaveAttribute("href", "/api/auth/login");
  });

  it("shows no dashboard data and no error by default", () => {
    render(<Login error={null} />);
    expect(
      screen.queryByText(/all good|backend unavailable/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows 'not authorized' for the unauthorized error (REQ-4.4)", () => {
    render(<Login error="unauthorized" />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      /this account is not authorized/i,
    );
  });

  it("shows a generic message for other errors (REQ-4.4)", () => {
    render(<Login error="login_failed" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/login failed/i);
  });
});
