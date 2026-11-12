declare module "page-flip" {
  export class PageFlip {
    constructor(element: HTMLElement, settings: Record<string, number | string | boolean>);
    loadFromHTML(pages: HTMLElement[]): void;
    on(event: string, callback: (event: { data: number | string | object }) => void): void;
    off(event: string): void;
    getCurrentPageIndex(): number;
    getState(): string;
    flip(page: number, corner?: "top" | "bottom"): void;
    turnToPage(page: number): void;
    destroy(): void;
  }
}
