"use client";

import { useEffect, useRef } from "react";
import styles from "./interviewer-avatar.module.css";

export type InterviewerAvatarStatus = "idle" | "connecting" | "opening" | "thinking" | "listening" | "speaking" | "paused" | "saving" | "failed";

type Props = {
  analyser: AnalyserNode | null;
  status: InterviewerAvatarStatus;
  className?: string;
};

const LABELS: Record<InterviewerAvatarStatus, string> = {
  idle: "忆谈书页伙伴，准备陪您聊聊",
  connecting: "忆谈书页伙伴，正在接通",
  opening: "忆谈书页伙伴，正在准备开场",
  thinking: "忆谈书页伙伴，正在想下一句话",
  listening: "忆谈书页伙伴，正在听您讲",
  speaking: "忆谈书页伙伴，正在说话",
  paused: "忆谈书页伙伴，声音已暂停",
  saving: "忆谈书页伙伴，正在保存访谈",
  failed: "忆谈书页伙伴，访谈暂时中断",
};

export function InterviewerAvatar({ analyser, status, className = "" }: Props) {
  const portrait = useRef<SVGSVGElement>(null);

  useEffect(() => {
    const svg = portrait.current;
    if (!svg) return;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let level = 0;
    let previousTime = 0;
    let samples = analyser ? new Float32Array(analyser.fftSize) : null;

    function reset() {
      window.cancelAnimationFrame(frame);
      frame = 0;
      level = 0;
      previousTime = 0;
      svg!.style.setProperty("--interviewer-mouth", "0");
      svg!.dataset.motion = "static";
    }
    function canMove() {
      return status === "speaking" && analyser !== null && analyser.context.state === "running" && document.visibilityState === "visible" && !reducedMotion.matches;
    }
    function draw(time: number) {
      frame = 0;
      if (!analyser || !canMove()) { reset(); return; }
      if (!samples || samples.length !== analyser.fftSize) samples = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(samples);
      let square = 0;
      for (const sample of samples) square += sample * sample;
      const rms = Math.sqrt(square / samples.length);
      const target = Math.max(0, Math.min(1, (20 * Math.log10(Math.max(rms, 0.000001)) + 52) / 33));
      const elapsed = previousTime ? Math.min(100, time - previousTime) : 16;
      previousTime = time;
      level += (target - level) * (1 - Math.exp(-elapsed / (target > level ? 40 : 90)));
      if (level < 0.005) level = 0;
      svg!.style.setProperty("--interviewer-mouth", level.toFixed(3));
      frame = window.requestAnimationFrame(draw);
    }
    function update() {
      reset();
      if (canMove()) {
        svg!.dataset.motion = "audio";
        frame = window.requestAnimationFrame(draw);
      }
    }

    reducedMotion.addEventListener("change", update);
    document.addEventListener("visibilitychange", update);
    analyser?.context.addEventListener("statechange", update);
    update();
    return () => {
      reducedMotion.removeEventListener("change", update);
      document.removeEventListener("visibilitychange", update);
      analyser?.context.removeEventListener("statechange", update);
      reset();
    };
  }, [analyser, status]);

  return <div className={`${styles.companion} ${className}`} data-status={status} role="img" aria-label={LABELS[status]}>
    <svg ref={portrait} className={styles.portrait} viewBox="0 0 192 196" fill="none" aria-hidden="true" focusable="false" data-motion="static">
      <ellipse className={styles.halo} cx="96" cy="99" rx="83" ry="79" fill="#ECE5D5" />
      <ellipse cx="96" cy="184" rx="56" ry="6" fill="#6C5A3B" opacity=".1" />
      <path d="M27 111C15 109 12 119 20 126L27 129M165 111C177 109 180 119 172 126L165 129" stroke="#5B7360" strokeWidth="5" strokeLinecap="round" />
      <path d="M72 166L69 181H59M120 166L123 181H133" stroke="#5B7360" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M25 48C47 37 72 37 96 47C120 37 145 37 167 48V162C143 157 119 160 96 172C73 160 49 157 25 162V48Z" fill="#587260" stroke="#425D4D" strokeWidth="2" strokeLinejoin="round" />
      <path d="M31 49C53 40 74 41 96 51C118 41 139 40 161 49V156C140 151 119 155 96 165C73 155 52 151 31 156V49Z" fill="#E9DFCC" />
      <path d="M34 46C55 38 75 41 96 51V160C74 151 55 148 34 152V46Z" fill="#FFF9EE" stroke="#D8C9AC" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M158 46C137 38 117 41 96 51V160C118 151 137 148 158 152V46Z" fill="#FCF4E4" stroke="#D8C9AC" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M96 52V75M96 137V158" stroke="#DCCDB2" strokeWidth="2" strokeLinecap="round" />
      <path className={styles.ribbon} d="M131 40C136 40 141 41 146 42V68L138.5 63L131 67V40Z" fill="#C3A065" />
      <path d="M44 60C52 57 60 56 68 57M45 139C55 137 65 138 75 141M45 145C54 144 62 145 69 147" stroke="#D8C8AA" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M127 141C132 133 140 132 144 135C138 135 134 138 132 144M128 145C132 143 137 144 139 148C134 148 130 147 127 145Z" fill="#9AAB8D" />
      <path className={styles.brows} d="M60 79C65 75 71 75 76 79M116 79C121 75 127 75 132 79" stroke="#6E5944" strokeWidth="2.5" strokeLinecap="round" />
      <ellipse cx="69" cy="94" rx="3.6" ry="5.2" fill="#4B3D32" />
      <ellipse cx="123" cy="94" rx="3.6" ry="5.2" fill="#4B3D32" />
      <circle cx="70" cy="92.5" r="1" fill="#FFFCF5" />
      <circle cx="124" cy="92.5" r="1" fill="#FFFCF5" />
      <ellipse cx="55" cy="108" rx="8" ry="4" fill="#DDA58F" opacity=".46" />
      <ellipse cx="137" cy="108" rx="8" ry="4" fill="#DDA58F" opacity=".46" />
      <path className={styles.smile} d="M83 116C90 124 102 124 109 116" stroke="#6B4C3C" strokeWidth="3.2" strokeLinecap="round" />
      <g className={styles.openMouth}>
        <ellipse cx="96" cy="118" rx="9" ry="8" fill="#70503F" />
        <ellipse cx="96" cy="122" rx="5.2" ry="2.4" fill="#D59E86" />
      </g>
    </svg>
  </div>;
}
