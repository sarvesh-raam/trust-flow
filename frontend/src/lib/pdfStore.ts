/**
 * In-memory / Session-based store for local PDFs.
 * Used to keep PDF files available for the viewer without round-tripping to the backend
 * or handling complex Blob URL lifecycles across reloads.
 */

const getPDFKey = (runId: string, source: string) => `pdf_cache_${runId}_${source}`;

export async function storePDF(runId: string, source: string, file: File): Promise<void> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        sessionStorage.setItem(getPDFKey(runId, source), reader.result as string);
        resolve();
      } catch (err) {
        console.warn("Storage limit exceeded, PDF not cached in session.");
        reject(err);
      }
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export function getPDF(runId: string, source: string): string | null {
  return sessionStorage.getItem(getPDFKey(runId, source));
}

export function clearPDF(runId: string, source: string): void {
  sessionStorage.removeItem(getPDFKey(runId, source));
}
