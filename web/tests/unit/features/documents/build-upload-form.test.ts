import { describe, expect, it } from "vitest";

import { buildUploadForm } from "@/features/documents/api";

/**
 * 上传表单的三条纪律（0016）：
 * 1. 空值一律不发字段（后端 `Form(datetime | None)` 收到空串会 422）；
 * 2. 时间转成带时区的 ISO（timestamptz 列与 `now()` 比较）；
 * 3. 权威度只接受 0–32767 的整数，其余静默丢弃、不让非法值出门。
 *
 * 直接单测 `buildUploadForm` 而不是在 MSW 里 `request.formData()`：
 * jsdom 的 FormData 与 Node fetch 跨 realm，undici 只会把它当普通对象序列化
 * （见 `tests/contract/api-client.test.ts` 的 apiUpload 用例），断言表单字段不可靠。
 */

function file(): File {
  return new File(["# 手册"], "手册.md", { type: "text/markdown" });
}

describe("features/documents/buildUploadForm", () => {
  it("不带可选项时只发 file，一个多余字段都没有", () => {
    const form = buildUploadForm(file());
    expect(form.get("file")).toBeInstanceOf(File);
    expect((form.get("file") as File).name).toBe("手册.md");
    expect(form.has("effective_from")).toBe(false);
    expect(form.has("effective_until")).toBe(false);
    expect(form.has("authority")).toBe(false);
  });

  it("空串 / 全空白的可选项都不发字段", () => {
    const form = buildUploadForm(file(), {
      effectiveFrom: "",
      effectiveUntil: "   ",
      authority: "",
    });
    expect(form.has("effective_from")).toBe(false);
    expect(form.has("effective_until")).toBe(false);
    expect(form.has("authority")).toBe(false);
  });

  it("datetime-local 值转成带 Z 的 ISO 字符串", () => {
    const form = buildUploadForm(file(), {
      effectiveFrom: "2026-11-10T09:00",
      effectiveUntil: "2027-01-01T00:00",
    });
    const from = form.get("effective_from");
    const until = form.get("effective_until");
    expect(typeof from).toBe("string");
    expect(typeof until).toBe("string");
    // 值随机器时区变化，只断言它是合法且带时区的 ISO，不钉死具体时刻。
    expect((from as string).endsWith("Z")).toBe(true);
    expect(Number.isNaN(Date.parse(from as string))).toBe(false);
    expect((until as string).endsWith("Z")).toBe(true);
  });

  it("非法时间字符串不发字段，不把 NaN 传给后端", () => {
    const form = buildUploadForm(file(), { effectiveFrom: "不是时间", effectiveUntil: "2026-13-99T99:99" });
    expect(form.has("effective_from")).toBe(false);
    expect(form.has("effective_until")).toBe(false);
  });

  it("合法权威度原样发整数；越界 / 非整数 / 非数字都不发", () => {
    expect(buildUploadForm(file(), { authority: "10" }).get("authority")).toBe("10");
    expect(buildUploadForm(file(), { authority: " 0 " }).get("authority")).toBe("0");
    expect(buildUploadForm(file(), { authority: "32767" }).get("authority")).toBe("32767");

    for (const bad of ["32768", "-1", "10.5", "abc", "1e3"]) {
      expect(buildUploadForm(file(), { authority: bad }).has("authority")).toBe(false);
    }
  });

  it("三项一起给时，file + 三个字段齐全", () => {
    const form = buildUploadForm(file(), {
      effectiveFrom: "2026-11-10T09:00",
      effectiveUntil: "2027-01-01T00:00",
      authority: "5",
    });
    expect(form.get("file")).toBeInstanceOf(File);
    expect(typeof form.get("effective_from")).toBe("string");
    expect(typeof form.get("effective_until")).toBe("string");
    expect(form.get("authority")).toBe("5");
  });
});
