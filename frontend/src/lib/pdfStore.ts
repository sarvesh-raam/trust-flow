/**
 * pdfStore — persist uploaded PDFs in sessionStorage as base64 data URLs
 * so they survive tab-switching within the same browser session.
 */

const key = (runId: string, type: "invoice" | "bl") => `pdf_${runId}_${type}`;

/** Store a PDF File as a base64 data URL (or fall back to object URL). */
export async function storePDF(
  runId: string,
  type: "invoice" | "bl",
  file: File,
): Promise<void> {
  return new Promise<void>((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        sessionStorage.setItem(key(runId, type), reader.result as string);
      } catch {
        // sessionStorage quota exceeded — store a short-lived object URL instead
        try {
          sessionStorage.setItem(
            `${key(runId, type)}_url`,
            URL.createObjectURL(file),
          );
        } catch {
          // Nothing we can do; viewer will show "NO PDF SOURCE AVAILABLE"
        }
      }
      resolve();
    };
    reader.readAsDataURL(file);
  });
}

/** Retrieve the stored PDF data URL (or object URL fallback), or null if absent. */
export function getPDF(runId: string, type: "invoice" | "bl"): string | null {
  return (
    sessionStorage.getItem(key(runId, type)) ??
    sessionStorage.getItem(`${key(runId, type)}_url`) ??
    null
  );
}

/** Remove all stored data for a given run+type pair. */
export function clearPDF(runId: string, type: "invoice" | "bl"): void {
  const url = sessionStorage.getItem(`${key(runId, type)}_url`);
  if (url?.startsWith("blob:")) URL.revokeObjectURL(url);
  sessionStorage.removeItem(key(runId, type));
  sessionStorage.removeItem(`${key(runId, type)}_url`);
}
