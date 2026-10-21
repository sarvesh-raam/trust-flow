import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { exchangeFirebaseToken as exchange } from "../lib/api";
import { auth, onAuthStateChanged, type User } from "../lib/firebase";

interface AuthContextType {
  user: User | null;
  loading: boolean;
}

const AuthContext = createContext<AuthContextType>({ user: null, loading: true });

// Backend access tokens last 30 min; refresh well before they expire.
// (The API client also re-exchanges on any 401 as a backstop.)
const TOKEN_REFRESH_MS = 25 * 60 * 1000;

async function exchangeFirebaseToken(u: User): Promise<void> {
  try {
    await exchange(u);
  } catch (err) {
    // Backend exchange failed — clear the stale token but keep the Firebase user
    // in context; the API client retries the exchange on the next 401.
    console.warn("[AuthContext] Backend JWT exchange failed (backend may be starting):", err);
    localStorage.removeItem("access_token");
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser]       = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, async (u) => {
      if (u) {
        // Exchange Firebase ID token for backend JWT (non-blocking — failure keeps user logged in)
        await exchangeFirebaseToken(u);
        setUser(u);
      } else {
        localStorage.removeItem("access_token");
        setUser(null);
      }
      setLoading(false);
    });
    return unsubscribe;
  }, []);

  // Refresh the backend JWT before its 30-minute expiry.
  useEffect(() => {
    if (!user) return;
    const interval = setInterval(async () => {
      await exchangeFirebaseToken(user);
    }, TOKEN_REFRESH_MS);
    return () => clearInterval(interval);
  }, [user]);

  if (loading) {
    return (
      <div style={{
        display:         "flex",
        alignItems:      "center",
        justifyContent:  "center",
        height:          "100vh",
        backgroundColor: "#06060b",
        color:           "#3B82F6",
        fontFamily:      "'JetBrains Mono', monospace",
        fontSize:        "0.75rem",
        letterSpacing:   "0.12em",
      }}>
        CUSTOMS DECLARATION — Authenticating…
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ user, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
