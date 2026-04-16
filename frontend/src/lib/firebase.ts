import { initializeApp } from "firebase/app";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signOut,
  onAuthStateChanged,
  type User,
} from "firebase/auth";

// Your web app's Firebase configuration
const firebaseConfig = {
  apiKey: "AIzaSyD7DZsOcJVM_cVbwlF-DucMeW1nEUNRpXY",
  authDomain: "hackstorm-1.firebaseapp.com",
  projectId: "hackstorm-1",
  storageBucket: "hackstorm-1.firebasestorage.app",
  messagingSenderId: "340935762110",
  appId: "1:340935762110:web:379d4064dd2315cf1cfeef",
  measurementId: "G-88F66ZQ3XS"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);

export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();

export const signInWithGoogle = () => signInWithPopup(auth, googleProvider);
export const signOutUser = () => signOut(auth);
export { onAuthStateChanged, type User };
