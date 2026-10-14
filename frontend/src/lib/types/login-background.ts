export type LoginBackgroundMode = "default" | "upload" | "urls";
export interface LoginBackground {
  mode: LoginBackgroundMode;
  urls: string[];
  interval_seconds: number;
  image_url: string | null;
}
