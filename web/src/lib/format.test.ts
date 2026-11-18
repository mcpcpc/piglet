import { describe, expect, it } from "vitest";
import { relativePath, splitPath } from "./format";

describe("paths", () => {
  it("makes paths inside the project relative", () => {
    expect(relativePath("/p/src/a.py", "/p")).toBe("src/a.py");
    expect(relativePath("/pother/a.py", "/p")).toBe("/pother/a.py");
    expect(relativePath("/p/a.py")).toBe("/p/a.py");
    expect(relativePath("C:\\p\\src\\a.ts", "C:\\p")).toBe("src\\a.ts");
  });

  it("splits off the file name so it can be shown first", () => {
    expect(splitPath("src/rate_limit.py")).toEqual({ dir: "src/", name: "rate_limit.py" });
    expect(splitPath("README.md")).toEqual({ dir: "", name: "README.md" });
    expect(splitPath("C:\\p\\a.ts")).toEqual({ dir: "C:\\p\\", name: "a.ts" });
  });
});
