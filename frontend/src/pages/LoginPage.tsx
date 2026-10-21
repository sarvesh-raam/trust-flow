import { useState } from "react";
import { createGuestSession } from "@/lib/api";
import { isFirebaseConfigured, signInWithGoogle } from "@/lib/firebase";

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  const handleSignIn = async () => {
    setLoading(true);
    setError(null);
    try {
      await signInWithGoogle();
      // AuthContext will detect the new user and re-render the protected app
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      // Ignore user-dismissed popup
      if (!msg.includes("popup-closed-by-user")) {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  };

  const handleGuestSignIn = async () => {
    setLoading(true);
    setError(null);
    try {
      await createGuestSession();
      window.location.reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight:       "100vh",
      backgroundColor: "#06060b",
      display:         "flex",
      alignItems:      "center",
      justifyContent:  "center",
      padding:         "24px 16px",
    }}>
      {/* Card */}
      <div style={{
        width:           "100%",
        maxWidth:        "400px",
        backgroundColor: "#0d0d1a",
        border:          "1px solid #1e293b",
        boxShadow:       "0 0 60px rgba(59,130,246,0.08)",
        overflow:        "hidden",
      }}>

        {/* Blue accent stripe */}
        <div style={{ height: "3px", backgroundColor: "#3B82F6" }} />

        <div style={{ padding: "36px 32px 32px" }}>
          {/* Logo */}
          <div style={{ textAlign: "center", marginBottom: "28px" }}>
            <div style={{
              fontFamily:    "'Space Grotesk', sans-serif",
              fontWeight:    700,
              fontSize:      "1.4rem",
              color:         "#3B82F6",
              letterSpacing: "0.03em",
              marginBottom:  "6px",
            }}>
              CUSTOMS DECLARATION
            </div>
            <div style={{
              fontFamily:    "'JetBrains Mono', monospace",
              fontSize:      "0.55rem",
              color:         "#334155",
              letterSpacing: "0.18em",
              textTransform: "uppercase",
            }}>
              HACKSTROM'26 | TRACK 3 | AUTONOMOUS COMPLIANCE AGENT
            </div>
          </div>

          {/* Description */}
          <p style={{
            fontFamily:    "'JetBrains Mono', monospace",
            fontSize:      "0.65rem",
            color:         "#475569",
            textAlign:     "center",
            lineHeight:    1.7,
            marginBottom:  "32px",
            letterSpacing: "0.02em",
          }}>
            AI-powered customs document processing<br />and compliance validation
          </p>

          {/* Google Sign-In button */}
          {isFirebaseConfigured && (
            <button
              onClick={handleSignIn}
              disabled={loading}
              style={{
                width:           "100%",
                display:         "flex",
                alignItems:      "center",
                justifyContent:  "center",
                gap:             "12px",
                padding:         "12px 20px",
                backgroundColor: loading ? "#0a0a12" : "#ffffff",
                border:          "1px solid #d1d5db",
                cursor:          loading ? "not-allowed" : "pointer",
                opacity:         loading ? 0.7 : 1,
                transition:      "background-color 0.15s ease",
                marginBottom:    "16px",
              }}
              onMouseEnter={(e) => {
                if (!loading) (e.currentTarget as HTMLElement).style.backgroundColor = "#f3f4f6";
              }}
              onMouseLeave={(e) => {
                if (!loading) (e.currentTarget as HTMLElement).style.backgroundColor = "#ffffff";
              }}
            >
              {!loading && (
                <svg width="18" height="18" viewBox="0 0 18 18">
                  <path fill="#4285F4" d="M16.51 8H8.98v3h4.3c-.18 1-.74 1.48-1.6 2.04v2.01h2.6a7.8 7.8 0 002.38-5.88c0-.57-.05-.66-.15-1.18z"/>
                  <path fill="#34A853" d="M8.98 17c2.16 0 3.97-.72 5.3-1.94l-2.6-2a4.8 4.8 0 01-7.18-2.54H1.83v2.07A8 8 0 008.98 17z"/>
                  <path fill="#FBBC05" d="M4.5 10.52a4.8 4.8 0 010-3.04V5.41H1.83a8 8 0 000 7.18l2.67-2.07z"/>
                  <path fill="#EA4335" d="M8.98 4.18c1.17 0 2.23.4 3.06 1.2l2.3-2.3A8 8 0 001.83 5.4L4.5 7.49a4.77 4.77 0 014.48-3.3z"/>
                </svg>
              )}

              <span style={{
                fontFamily:    "'JetBrains Mono', monospace",
                fontSize:      "0.72rem",
                fontWeight:    700,
                color:         loading ? "#475569" : "#1e293b",
                letterSpacing: "0.04em",
              }}>
                {loading ? "SIGNING IN…" : "Continue with Google"}
              </span>
            </button>
          )}

          <button
            onClick={handleGuestSignIn}
            disabled={loading}
            style={{
              width:           "100%",
              padding:         "11px 20px",
              backgroundColor: "transparent",
              border:          "1px solid #1e3a5f",
              color:           "#60a5fa",
              cursor:          loading ? "not-allowed" : "pointer",
              opacity:         loading ? 0.7 : 1,
              fontFamily:      "'JetBrains Mono', monospace",
              fontSize:        "0.7rem",
              fontWeight:      700,
              letterSpacing:   "0.08em",
              marginBottom:    "12px",
            }}
          >
            Continue Locally
          </button>

          {/* Error message */}
          {error && (
            <div style={{
              padding:         "8px 12px",
              backgroundColor: "rgba(239,68,68,0.06)",
              border:          "1px solid rgba(239,68,68,0.2)",
              borderLeft:      "3px solid #ef4444",
              marginBottom:    "16px",
            }}>
              <p style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize:   "0.6rem",
                color:      "#ef4444",
                margin:     0,
              }}>
                {error}
              </p>
            </div>
          )}

          {/* Footer */}
          <div style={{ textAlign: "center", marginTop: "24px" }}>
            <p style={{
              fontFamily:    "'JetBrains Mono', monospace",
              fontSize:      "0.55rem",
              color:         "#1e293b",
              letterSpacing: "0.06em",
              marginBottom:  "4px",
            }}>
              {isFirebaseConfigured
                ? "Secure authentication powered by Firebase"
                : "Firebase is not configured — local guest mode available"}
            </p>
            <p style={{
              fontFamily:    "'JetBrains Mono', monospace",
              fontSize:      "0.52rem",
              color:         "#0f172a",
              letterSpacing: "0.06em",
            }}>
              v0.1.0 · Hackstrom&apos;26 Hackathon
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
