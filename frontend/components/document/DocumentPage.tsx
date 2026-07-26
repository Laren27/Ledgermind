"use client";
import { useState, useEffect, useRef } from "react";
import { PaperStack } from "@/components/environment/PaperStack";

export type ShiftPhase = null | "exiting-next" | "exiting-prev" | "settling";

interface DocumentPageProps {
  docId: string;
  pageNumber: number;
  totalPages: number;
  confidential?: boolean;
  isLoading?: boolean;
  children: React.ReactNode;
  footerLabelOverride?: string;
  isShifting?: boolean;
  shiftPhase?: ShiftPhase;
  onSheetTransitionEnd?: () => void;
  underneathContent?: React.ReactNode;
  underneathPageNumber?: number;
  underneathDocId?: string;
}

export function DocumentPage({
  docId,
  pageNumber,
  totalPages,
  confidential,
  isLoading,
  children,
  footerLabelOverride,
  isShifting = false,
  shiftPhase = null,
  onSheetTransitionEnd,
  underneathContent,
  underneathPageNumber,
  underneathDocId,
}: DocumentPageProps) {
  const [isHovered, setIsHovered] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);

  const isCompressing = shiftPhase === "exiting-next" || shiftPhase === "exiting-prev";

  // 💡 WEB ANIMATIONS API INTEGRATION: Exact 720ms Exit Flight + 35ms Settle
  useEffect(() => {
    if (!sheetRef.current || !shiftPhase) return;
    const sheet = sheetRef.current;

    // Shadow States: Separated RISE and PEAK to create momentum after visual lift
    const REST      = "0 12px 18px rgba(0,0,0,0.18)";
    const TIGHT     = "0 8px 14px rgba(0,0,0,0.22)";   // anticipation: pressed down
    const LIFT_RISE = "0 38px 64px rgba(0,0,0,0.28)";  // apex reach
    const LIFT_PEAK = "0 46px 76px rgba(0,0,0,0.33)";  // peaks AFTER vertical motion stops
    const LAND_SH   = "0 14px 20px rgba(0,0,0,0.20)";  // landing: shadow narrows back down
    const BASE      = "rotateX(2.5deg) rotateY(-1.2deg)";

    if (shiftPhase === "exiting-next" || shiftPhase === "exiting-prev") {
      const isNext = shiftPhase === "exiting-next";
      
      // Dynamically compute travel distance based on rendered width (30% x, -19% y)
      const w = sheet.offsetWidth || 1000;
      const dx = (isNext ? 1 : -1) * (w * 0.30);
      const dy = -(w * 0.19);
      const rotZ = isNext ? "3deg" : "-3deg";
      const destTransform = `translate(${dx}px, ${dy}px) rotateZ(${rotZ}) scale(0.9)`;

      const liftRot = isNext ? "0.8deg" : "-0.8deg";
      const liftTransform = `translate(0, -26px) rotateZ(${liftRot}) scale(1.012)`;

      // Timeline offsets: 45ms Anticipate -> 180ms Lift -> 60ms Plateau -> 435ms Glide = 720ms
      const keyframes: Keyframe[] = [
        { transform: `scale(1) translate(0,0) rotateZ(0deg) ${BASE}`, boxShadow: REST, easing: "ease-in", offset: 0 },
        { transform: `scale(0.997) translate(0,1px) rotateZ(0deg) ${BASE}`, boxShadow: TIGHT, easing: "cubic-bezier(0.22, 0.8, 0.22, 1)", offset: 45 / 720 },
        { transform: `${liftTransform} ${BASE}`, boxShadow: LIFT_RISE, easing: "linear", offset: 225 / 720 },
        // 💡 60ms Apex Hold Plateau: Zero spatial movement while shadow expands to LIFT_PEAK
        { transform: `${liftTransform} ${BASE}`, boxShadow: LIFT_PEAK, easing: "cubic-bezier(0.4, 0, 0.2, 1)", offset: 285 / 720 },
        { transform: `${destTransform} ${BASE}`, boxShadow: LAND_SH, offset: 1 },
      ];

      const anim = sheet.animate(keyframes, { duration: 720, fill: "forwards" });
      
      // Decoupled Late Opacity Fade (Fades naturally during last ~12% of travel)
      const fadeAnim = sheet.animate([
        { opacity: 1, offset: 0 },
        { opacity: 1, offset: 0.88 },
        { opacity: 0.65, offset: 0.96 },
        { opacity: 0, offset: 1 }
      ], { duration: 720, easing: 'ease-out', fill: "forwards" });

      anim.onfinish = () => {
        // 💡 STRIP COMMIT STYLES: Do NOT call commitStyles() on exit flight!
        // Cancelling detaches WAAPI so React's style prop (opacity: 1, center desk) takes over instantly.
        anim.cancel();
        fadeAnim.cancel();
        sheet.style.opacity = "1";
        onSheetTransitionEnd?.();
      };
      return () => { anim.cancel(); fadeAnim.cancel(); };
    } else if (shiftPhase === "settling") {
      // 💡 35ms SETTLE: Pure shadow relaxation; text is already rendered synchronously
      const keyframes: Keyframe[] = [
        { boxShadow: LAND_SH },
        { boxShadow: REST }
      ];
      const anim = sheet.animate(keyframes, { duration: 35, fill: "forwards", easing: "ease-out" });
      anim.onfinish = () => {
        anim.cancel();
        onSheetTransitionEnd?.();
      };
      return () => anim.cancel();
    }
  }, [shiftPhase, onSheetTransitionEnd]);

  const baseTransform = isHovered && !shiftPhase
    ? "perspective(1800px) rotateX(1.8deg) rotateY(-0.8deg) rotateZ(-0.15deg) translateY(-4px)"
    : "perspective(1800px) rotateX(2.5deg) rotateY(-1.2deg) rotateZ(-0.35deg) translateY(0px)";

  return (
    <div
      className="relative mb-16 select-none"
      style={{ 
        width: "93%", 
        maxWidth: 1140, 
        marginTop: "50px", 
        marginLeft: "4.5%", 
        marginRight: "auto" 
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Layer 4: Ream Stack with Dynamic Friction Compression & Containing Block Anchor */}
      <div className={`absolute inset-0 pointer-events-none z-0 transition-transform duration-[240ms] ease-[cubic-bezier(0.16,1,0.3,1)] ${isCompressing ? "translate-y-[5px] scale-y-[0.995]" : ""}`}>
        <PaperStack />
      </div>

      {/* 💡 Layer 4.5: Persistent Under-Sheet Canvas (Identical Cream Styling & Pre-Loaded Content) */}
      <div
        className="absolute inset-0 z-[5] flex flex-col justify-between rounded-sm overflow-hidden pointer-events-none"
        style={{
          background: "var(--theme-surface-paper, #E7DED0)",
          border: "1px solid var(--theme-border-subtle)",
          borderRadius: "var(--radius-sm, 3px)",
          boxShadow: "var(--shadow-paper-rest)",
          padding: "var(--rhythm-major, 72px) var(--space-12, 48px) var(--space-12, 48px)",
          fontFamily: "var(--font-ui, sans-serif)",
          fontSize: "var(--font-size-body, 18px)",
          color: "var(--ink-primary, #2A241E)",
          lineHeight: 1.6,
          transform: baseTransform,
          transformOrigin: "42% 60%",
          opacity: 1,
        }}
      >
        {/* Identical Subconscious Texture Overlay */}
        <div
          className="pointer-events-none absolute inset-0 z-0"
          style={{
            backgroundImage: `url('/assets/environment/paper-texture.png')`,
            backgroundSize: "cover",
            backgroundPosition: "center",
            opacity: 0.018,
            mixBlendMode: "multiply",
          }}
        />

        {/* 30px Precision Fold Corner */}
        <div
          className="absolute top-0 right-0 pointer-events-none z-10"
          style={{
            width: "30px",
            height: "30px",
            background: "linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.08) 50%)",
            clipPath: "polygon(100% 0, 0 0, 100% 100%)",
          }}
        />

        {/* STRICT VERTICAL RHYTHM SECTION CONTENT (Pre-loaded Underneath Page) */}
        <div className="relative z-10 flex-1 flex flex-col justify-between space-y-[var(--rhythm-major,72px)]">
          <div>{underneathContent || children}</div>
        </div>

        {/* Archival Stamp Watermark (~1.8% Opacity) */}
        <div
          className="pointer-events-none absolute select-none z-0 py-4 px-8 border-y-[1.5px]"
          style={{
            bottom: "26%", right: "7%",
            fontFamily: "var(--font-archival, monospace)",
            color: "var(--ink-primary)",
            borderColor: "var(--ink-primary)",
            opacity: 0.018,
            transform: "rotate(-10deg)",
          }}
        >
          <div className="text-3xl font-extrabold tracking-[0.32em] uppercase text-center">LEDGERMIND</div>
          <div className="text-[11px] tracking-[0.40em] font-semibold text-center mt-1.5 uppercase">Working Paper • Verified</div>
        </div>

        {/* Engraved Power-User Footer (Strict Dynamic Mapping) */}
        <div
          className="relative z-10 mt-[var(--rhythm-major,72px)] flex items-center justify-between border-t pt-4 font-normal"
          style={{ 
            borderColor: "var(--ink-divider)", 
            fontFamily: "var(--font-archival, monospace)", 
            fontSize: "var(--font-size-footer, 12px)", 
            color: "var(--ink-footer)",
          }}
        >
          <span>DOC ID: {underneathDocId ?? docId}</span>
          {confidential && <span>CONFIDENTIAL — INTERNAL USE ONLY</span>}
          <span className="tabular-metrics">{footerLabelOverride ?? `PAGE ${underneathPageNumber ?? pageNumber} OF ${totalPages}`}</span>
        </div>
      </div>

      {/* Layer 5: Active Working Paper Canvas (Permanent 42% 60% Pivot & GPU Hints) */}
      <div
        ref={sheetRef}
        className="relative z-10 flex flex-col justify-between rounded-sm overflow-hidden"
        style={{
          background: "var(--theme-surface-paper, #E7DED0)",
          border: "1px solid var(--theme-border-subtle)",
          borderRadius: "var(--radius-sm, 3px)",
          boxShadow: isHovered || isCompressing ? "var(--shadow-paper-hover)" : "var(--shadow-paper-rest)",
          padding: "var(--rhythm-major, 72px) var(--space-12, 48px) var(--space-12, 48px)",
          minHeight: 1080,
          height: "auto",
          fontFamily: "var(--font-ui, sans-serif)",
          fontSize: "var(--font-size-body, 18px)",
          color: "var(--ink-primary, #2A241E)",
          lineHeight: 1.6,
          transform: baseTransform,
          transformOrigin: "42% 60%",
          willChange: "transform, box-shadow",
          opacity: 1, // ZERO FADES: Paper remains 100% solid at all times
        }}
      >
        {/* Subconscious Texture Overlay */}
        <div
          className="pointer-events-none absolute inset-0 z-0"
          style={{
            backgroundImage: `url('/assets/environment/paper-texture.png')`,
            backgroundSize: "cover",
            backgroundPosition: "center",
            opacity: 0.018,
            mixBlendMode: "multiply",
          }}
        />

        {/* 30px Precision Fold Corner */}
        <div
          className="absolute top-0 right-0 pointer-events-none z-10"
          style={{
            width: "30px",
            height: "30px",
            background: "linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.08) 50%)",
            clipPath: "polygon(100% 0, 0 0, 100% 100%)",
          }}
        />

        {/* STRICT VERTICAL RHYTHM SECTION CONTENT (Zero Fabricated Mockups) */}
        <div className="relative z-10 flex-1 flex flex-col justify-between space-y-[var(--rhythm-major,72px)]">
          <div>{children}</div>
        </div>

        {/* Archival Stamp Watermark (~1.8% Opacity) */}
        <div
          className="pointer-events-none absolute select-none z-0 py-4 px-8 border-y-[1.5px]"
          style={{
            bottom: "26%", right: "7%",
            fontFamily: "var(--font-archival, monospace)",
            color: "var(--ink-primary)",
            borderColor: "var(--ink-primary)",
            opacity: 0.018,
            transform: "rotate(-10deg)",
          }}
        >
          <div className="text-3xl font-extrabold tracking-[0.32em] uppercase text-center">LEDGERMIND</div>
          <div className="text-[11px] tracking-[0.40em] font-semibold text-center mt-1.5 uppercase">Working Paper • Verified</div>
        </div>

        {/* Engraved Power-User Footer (Strict Dynamic Mapping) */}
        <div
          className="relative z-10 mt-[var(--rhythm-major,72px)] flex items-center justify-between border-t pt-4 font-normal"
          style={{ 
            borderColor: "var(--ink-divider)", 
            fontFamily: "var(--font-archival, monospace)", 
            fontSize: "var(--font-size-footer, 12px)", 
            color: "var(--ink-footer)",
          }}
        >
          <span>DOC ID: {docId}</span>
          {confidential && <span>CONFIDENTIAL — INTERNAL USE ONLY</span>}
          <span className="tabular-metrics">{footerLabelOverride ?? `PAGE ${pageNumber} OF ${totalPages}`}</span>
        </div>
      </div>
    </div>
  );
}
