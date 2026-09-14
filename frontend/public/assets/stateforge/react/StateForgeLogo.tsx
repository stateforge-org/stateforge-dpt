import { forwardRef, useId, type SVGProps } from "react";

export interface StateForgeLogoProps
  extends Omit<SVGProps<SVGSVGElement>, "width" | "height"> {
  /** Accessible name. Omit when an adjacent text label already names the brand. */
  title?: string;
  /** CSS length or unitless pixel size. */
  size?: number | string;
}

export const StateForgeLogo = forwardRef<SVGSVGElement, StateForgeLogoProps>(
  function StateForgeLogo(
    { title, size = 48, ...props },
    ref,
  ) {
    const instanceId = useId().replace(/:/g, "");
    const titleId = `stateforge-title-${instanceId}`;
    const descriptionId = `stateforge-description-${instanceId}`;
    const upperFadeId = `stateforge-upper-fade-${instanceId}`;
    const lowerFadeId = `stateforge-lower-fade-${instanceId}`;
    const glowId = `stateforge-glow-${instanceId}`;
    const prominentFilter = `url(#${glowId})`;

    return (
      <svg
        ref={ref}
        width={size}
        height={size}
        viewBox="0 0 100 100"
        fill="none"
        color="#7FBCFB"
        xmlns="http://www.w3.org/2000/svg"
        role={title ? "img" : undefined}
        aria-labelledby={title ? `${titleId} ${descriptionId}` : undefined}
        aria-hidden={title ? undefined : true}
        focusable="false"
        {...props}
      >
        {title ? <title id={titleId}>{title}</title> : null}
        {title ? (
          <desc id={descriptionId}>
            A cube containing a layered state checkpoint.
          </desc>
        ) : null}
        <defs>
          <filter
            id={glowId}
            x="-35%"
            y="-35%"
            width="170%"
            height="170%"
          >
            <feGaussianBlur
              in="SourceGraphic"
              stdDeviation="0.72"
              result="softGlow"
            />
            <feMerge>
              <feMergeNode in="softGlow" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          <linearGradient
            id={upperFadeId}
            gradientUnits="userSpaceOnUse"
            x1="27"
            y1="48"
            x2="27"
            y2="55"
          >
            <stop offset="0" stopColor="currentColor" stopOpacity="0.4" />
            <stop offset="0.48" stopColor="currentColor" stopOpacity="0" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
          <linearGradient
            id={lowerFadeId}
            gradientUnits="userSpaceOnUse"
            x1="27"
            y1="55"
            x2="27"
            y2="62"
          >
            <stop offset="0" stopColor="currentColor" stopOpacity="0.32" />
            <stop offset="0.48" stopColor="currentColor" stopOpacity="0" />
            <stop offset="1" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>

        <path
          d="M50 11 85 31v38L50 89 15 69V31Z"
          stroke="currentColor"
          strokeWidth="2.35"
          strokeLinecap="round"
          strokeLinejoin="miter"
          vectorEffect="non-scaling-stroke"
          filter={prominentFilter}
        />
        <path
          d="M50 11v23M15 69l24.1-13.6M85 69 60.9 55.4"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="miter"
          vectorEffect="non-scaling-stroke"
          opacity="0.3"
        />
        <path
          d="M27 48v7M73 48v7"
          stroke={`url(#${upperFadeId})`}
          strokeWidth="1.25"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        <path
          d="M27 55v7M73 55v7"
          stroke={`url(#${lowerFadeId})`}
          strokeWidth="1.15"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        <g filter={prominentFilter}>
          <path
            d="m50 34 23 14-23 14-23-14Z"
            fill="currentColor"
            fillOpacity="0.07"
            stroke="currentColor"
            strokeWidth="2.65"
            strokeLinecap="round"
            strokeLinejoin="miter"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d="m27 55 23 14 23-14"
            stroke="currentColor"
            strokeWidth="2.35"
            strokeLinecap="round"
            strokeLinejoin="miter"
            vectorEffect="non-scaling-stroke"
            opacity="0.74"
          />
          <path
            d="m27 62 23 14 23-14"
            stroke="currentColor"
            strokeWidth="2.05"
            strokeLinecap="round"
            strokeLinejoin="miter"
            vectorEffect="non-scaling-stroke"
            opacity="0.48"
          />
        </g>
        <path
          d="M50 34v28M27 48l23 3 23-3"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
          opacity="0.38"
        />
      </svg>
    );
  },
);
