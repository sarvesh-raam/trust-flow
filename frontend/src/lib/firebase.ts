import { initializeApp, type FirebaseApp } from "firebase/app";
import { getAuth, signInWithPopup, GoogleAuthProvider, onAuthStateChanged, signOut, type Auth, type User } from "firebase/auth";

const firebaseConfig = {
  apiKey:            import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain:        import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId:         import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket:     import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId:             import.meta.env.VITE_FIREBASE_APP_ID,
};

export const isFirebaseConfigured = Object.values(firebaseConfig).every(Boolean);

let app: FirebaseApp | null = null;
let auth: Auth | null = null;
let provider: GoogleAuthProvider | null = null;

if (isFirebaseConfigured) {
  app = initializeApp(firebaseConfig);
  auth = getAuth(app);
  provider = new GoogleAuthProvider();
}

export const signInWithGoogle = () => {
  if (!auth || !provider) throw new Error("Firebase is not configured.");
  return signInWithPopup(auth, provider);
};

export const signOutUser = async () => {
  localStorage.removeItem("guest_session");
  localStorage.removeItem("access_token");
  try {
    if (auth) await signOut(auth);
  } catch {
    // Local guest sessions do not have a Firebase user to sign out.
  }
};

export { auth, onAuthStateChanged, type User };
