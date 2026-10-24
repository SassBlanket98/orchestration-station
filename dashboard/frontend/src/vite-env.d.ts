/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" builds the artifact: always mock data, in-memory router, no server. */
  readonly VITE_ARTIFACT?: string;
}
