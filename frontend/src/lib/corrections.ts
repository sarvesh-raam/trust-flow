import type { ComplianceIssue } from "./api";

/** One input in the human-review panel, keyed by the backend correction path. */
export interface CorrectionField {
  path: string;
  label: string;
  numeric: boolean;
  placeholder?: string;
}

const INVOICE_FIELDS = [
  "invoice_number", "date", "seller", "buyer", "total_amount", "currency", "gross_weight_kg",
];
const BL_FIELDS = [
  "bl_number", "vessel", "port_of_loading", "port_of_discharge", "gross_weight_kg", "consignee", "shipper",
];
const NUMERIC = new Set(["total_amount", "gross_weight_kg"]);
const PLACEHOLDERS: Record<string, string> = {
  date:            "YYYY-MM-DD",
  currency:        "3-letter code, e.g. USD",
  gross_weight_kg: "kg",
  hs_code:         "e.g. 8413.70.1000",
};
const HS_PATH = /^line_items\.(\d+)\.hs_code$/;

function describe(path: string): CorrectionField {
  const hs = HS_PATH.exec(path);
  if (hs) {
    return { path, label: `LINE ITEM ${Number(hs[1]) + 1} · HS CODE`, numeric: false, placeholder: PLACEHOLDERS.hs_code };
  }
  const [doc, field] = path.split(".");
  const docLabel = doc === "invoice" ? "INVOICE" : "B/L";
  const unit = field === "gross_weight_kg" ? " (KG)" : "";
  return {
    path,
    label: `${docLabel} · ${field.replace(/_kg$/, "").replace(/_/g, " ").toUpperCase()}${unit}`,
    numeric: NUMERIC.has(field),
    placeholder: PLACEHOLDERS[field],
  };
}

function isCorrectable(path: string): boolean {
  if (HS_PATH.test(path)) return true;
  const [doc, field, ...rest] = path.split(".");
  if (rest.length) return false;
  return (doc === "invoice" && INVOICE_FIELDS.includes(field)) || (doc === "bill_of_lading" && BL_FIELDS.includes(field));
}

/** Paths an operator can edit to resolve one issue (empty if none apply). */
function pathsForIssue(issue: ComplianceIssue): string[] {
  switch (issue.field) {
    case "gross_weight_kg":                       // invoice vs B/L weight mismatch
      return ["invoice.gross_weight_kg", "bill_of_lading.gross_weight_kg"];
    case "invoice":                               // invoice could not be extracted
      return INVOICE_FIELDS.map((f) => `invoice.${f}`);
    case "bill_of_lading":                        // B/L could not be extracted
      return BL_FIELDS.map((f) => `bill_of_lading.${f}`);
    default:
      return isCorrectable(issue.field) ? [issue.field] : [];
  }
}

/** Unique input fields needed to resolve every blocking issue, in issue order. */
export function correctionFieldsFor(issues: ComplianceIssue[]): CorrectionField[] {
  const paths = [...new Set(issues.flatMap(pathsForIssue))];
  return paths.map(describe);
}

/** Current extracted value for a path, as a string for an input box. */
export function currentValue(result: Record<string, any>, path: string): string {
  const hs = HS_PATH.exec(path);
  const value = hs
    ? result.invoice?.line_items?.[Number(hs[1])]?.hs_code
    : result[path.split(".")[0]]?.[path.split(".")[1]];
  if (value === null || value === undefined) return "";
  // A zero weight/amount means "missing" — show an empty box, not "0".
  return typeof value === "number" && value <= 0 ? "" : String(value);
}

/**
 * Turn the form's string values into the API payload.
 * Returns an error message instead when a value is invalid.
 */
export function buildCorrections(
  fields: CorrectionField[],
  values: Record<string, string>,
): { corrections: Record<string, string | number> } | { error: string } {
  const corrections: Record<string, string | number> = {};
  for (const f of fields) {
    const raw = (values[f.path] ?? "").trim();
    if (!f.numeric) {
      corrections[f.path] = raw;
      continue;
    }
    const n = Number(raw);
    if (raw === "" || !Number.isFinite(n) || n <= 0) {
      return { error: `${f.label}: enter a number greater than 0.` };
    }
    corrections[f.path] = n;
  }
  return { corrections };
}
