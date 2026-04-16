import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { auth, onAuthStateChanged, type User } from "../lib/firebase";

interface AuthContextType {
  user: User | null;
  loading: boolean;
}

const AuthContext = createContext<AuthContextType>({ user: null, loading: true });

async function exchangeFirebaseToken(u: User): Promise<void> {
  try {
    const idToken = await u.getIdToken(/* forceRefresh */ false);
    const { data } = await api.post("/auth/google", { firebase_token: idToken });
    localStorage.setItem("access_token", data.access_token);
  } catch (err) {
    // Backend exchange failed — clear stale token but keep Firebase user in context.
    // The UI is still protected by Firebase auth; backend routes are unprotected for hackathon.
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

  // Auto-refresh backend JWT when Firebase token renews (every ~55 min)
  useEffect(() => {
    if (!user) return;
    const interval = setInterval(async () => {
      await exchangeFirebaseToken(user);
    }, 55 * 60 * 1000);
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
        ◈ CUSTOMS DECL — Authenticating…
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
