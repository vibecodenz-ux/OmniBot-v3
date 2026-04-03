import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, ColorType, LineStyle, MismatchDirection, createChart, type MouseEventParams, type Time, type UTCTimestamp } from "lightweight-charts";
import { formatChartTime, formatMoney, formatTimestamp } from "../lib/format";
import type { StrategyActivityCandle, StrategyActivityPositionOverlay } from "../lib/types";

type ThemeMode = "dark" | "light";

interface CandlestickChartProps {
  symbol: string;
  timeframe?: string | null;
  candles: StrategyActivityCandle[];
  overlays?: StrategyActivityPositionOverlay[];
  themeMode: ThemeMode;
}

interface ChartBarView {
  time: UTCTimestamp;
  openedAt: string | null;
  label: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

function toChartTimestamp(openedAt: string | undefined, fallbackIndex: number): UTCTimestamp {
  if (openedAt) {
    const timestamp = Math.floor(new Date(openedAt).getTime() / 1000);
    if (Number.isFinite(timestamp) && timestamp > 0) {
      return timestamp as UTCTimestamp;
    }
  }

  return (fallbackIndex + 1) as UTCTimestamp;
}

function formatChartTick(time: Time, labelByTime?: Map<number, string>): string {
  if (typeof time === "number") {
    const knownLabel = labelByTime?.get(Number(time));
    if (knownLabel) {
      return knownLabel;
    }

    return formatChartTime(new Date(time * 1000).toISOString());
  }

  if (typeof time === "string") {
    return formatChartTime(new Date(time).toISOString());
  }

  if (typeof time === "object" && time !== null && "year" in time) {
    return formatChartTime(new Date(Date.UTC(time.year, time.month - 1, time.day)).toISOString());
  }

  return "";
}

export function CandlestickChart({ symbol, timeframe, candles, overlays, themeMode }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  const palette = useMemo(() => {
    if (themeMode === "light") {
      return {
        text: "#6f7e8f",
        grid: "rgba(73, 91, 116, 0.11)",
        border: "rgba(73, 91, 116, 0.14)",
        crosshair: "rgba(66, 104, 199, 0.26)",
        up: "#159b5d",
        down: "#d55353",
        overlayEntry: "rgba(208, 139, 24, 0.95)",
        overlayTarget: "rgba(21, 155, 93, 0.95)",
      };
    }

    return {
      text: "#91a3c0",
      grid: "rgba(128, 151, 188, 0.12)",
      border: "rgba(128, 151, 188, 0.16)",
      crosshair: "rgba(95, 134, 255, 0.28)",
      up: "#2bc278",
      down: "#ff7a7a",
      overlayEntry: "rgba(245, 207, 92, 0.92)",
      overlayTarget: "rgba(111, 227, 180, 0.96)",
    };
  }, [themeMode]);

  const chartData = useMemo(() => candles.map((candle, index) => {
    const open = Number(candle.open);
    const high = Number(candle.high);
    const low = Number(candle.low);
    const close = Number(candle.close);

    if (![open, high, low, close].every((value) => Number.isFinite(value))) {
      return null;
    }

    return {
      time: toChartTimestamp(candle.opened_at, index),
      openedAt: candle.opened_at || null,
      label: candle.opened_at ? formatChartTime(candle.opened_at) : candle.label,
      open,
      high,
      low,
      close,
    };
  }).filter((candle): candle is ChartBarView => candle !== null).sort((left, right) => Number(left.time) - Number(right.time)), [candles]);

  const resolvedOverlays = useMemo(() => (overlays || []).flatMap((overlay) => {
    const entryPrice = Number(overlay.entry_price);
    const closeTargetPrice = Number(overlay.close_target_price);

    return [
      Number.isFinite(entryPrice)
        ? { label: "Entry", price: entryPrice, className: "chart-overlay-entry", color: palette.overlayEntry }
        : null,
      Number.isFinite(closeTargetPrice)
        ? { label: "Planned close", price: closeTargetPrice, className: "chart-overlay-target", color: palette.overlayTarget }
        : null,
    ].filter((item): item is { label: string; price: number; className: string; color: string } => item !== null);
  }), [overlays, palette.overlayEntry, palette.overlayTarget]);

  const labelByTime = useMemo(
    () => new Map(chartData.map((candle) => [Number(candle.time), candle.label])),
    [chartData],
  );
  const openedAtByTime = useMemo(
    () => new Map(chartData.map((candle) => [Number(candle.time), candle.openedAt])),
    [chartData],
  );
  const [hoveredBar, setHoveredBar] = useState<ChartBarView | null>(null);

  useEffect(() => {
    setHoveredBar(null);
  }, [chartData]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || chartData.length < 2) {
      return undefined;
    }

    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 240),
      height: 236,
      layout: {
        textColor: palette.text,
        background: { type: ColorType.Solid, color: "transparent" },
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      crosshair: {
        vertLine: { color: palette.crosshair, width: 1, labelVisible: true },
        horzLine: { color: palette.crosshair, width: 1, labelVisible: true },
      },
      rightPriceScale: {
        borderColor: palette.border,
        scaleMargins: { top: 0.14, bottom: 0.12 },
      },
      leftPriceScale: {
        visible: false,
      },
      timeScale: {
        borderColor: palette.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 1,
        barSpacing: 18,
        minBarSpacing: 10,
        tickMarkFormatter: (time: Time) => formatChartTick(time, labelByTime),
      },
      localization: {
        priceFormatter: (price: number) => formatMoney(price),
        timeFormatter: (time: Time) => formatChartTick(time, labelByTime),
      },
      handleScroll: {
        mouseWheel: false,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: false,
        mouseWheel: false,
        pinch: true,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: palette.up,
      downColor: palette.down,
      borderVisible: false,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    candleSeries.setData(chartData.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));

    resolvedOverlays.forEach((overlay) => {
      candleSeries.createPriceLine({
        price: overlay.price,
        color: overlay.color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: overlay.label,
      });
    });

    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (!param.point || param.logical === undefined) {
        setHoveredBar(null);
        return;
      }

      const rawBar = candleSeries.dataByIndex(Math.round(Number(param.logical)), MismatchDirection.NearestLeft);
      if (!rawBar || !("open" in rawBar) || !("high" in rawBar) || !("low" in rawBar) || !("close" in rawBar)) {
        setHoveredBar(null);
        return;
      }

      const timeValue = Number(rawBar.time);
      setHoveredBar({
        time: rawBar.time as UTCTimestamp,
        openedAt: openedAtByTime.get(timeValue) || null,
        label: labelByTime.get(timeValue) || formatChartTick(rawBar.time, labelByTime),
        open: Number(rawBar.open),
        high: Number(rawBar.high),
        low: Number(rawBar.low),
        close: Number(rawBar.close),
      });
    };

    chart.subscribeCrosshairMove(handleCrosshairMove);

    chart.timeScale().fitContent();

    const resize = () => {
      chart.applyOptions({ width: Math.max(container.clientWidth, 240) });
    };

    resize();

    const resizeObserver = new ResizeObserver(() => resize());
    resizeObserver.observe(container);

    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [chartData, labelByTime, openedAtByTime, palette, resolvedOverlays]);

  if (chartData.length < 2) {
    return <div className="sparkline-empty">Awaiting candle data.</div>;
  }

  const latest = chartData.at(-1);
  const activeBar = hoveredBar || latest;

  return (
    <div className="series-chart-block">
      <div className="series-chart-header">
        <span>{symbol}</span>
        <small>{timeframe || "5m"} candles</small>
      </div>
      {resolvedOverlays.length ? (
        <div className="chart-overlay-legend" aria-label={`${symbol} position levels`}>
          {resolvedOverlays.map((overlay) => (
            <span key={`${overlay.label}-${overlay.price}`} className={`chart-overlay-pill ${overlay.className}`}>
              {`${overlay.label} ${formatMoney(overlay.price)}`}
            </span>
          ))}
        </div>
      ) : null}
      <div className="sparkline-shell candlestick-shell">
        <div ref={containerRef} className="candlestick-host" aria-label={`${symbol} candlestick chart`} />
        <div className="sparkline-meta">
          <span title={activeBar?.openedAt ? formatTimestamp(activeBar.openedAt) : undefined}>{activeBar?.label || "Latest"}</span>
          <strong>{`O ${formatMoney(activeBar?.open)} H ${formatMoney(activeBar?.high)} L ${formatMoney(activeBar?.low)} C ${formatMoney(activeBar?.close)}`}</strong>
        </div>
        <div className="chart-attribution">
          <a href="https://www.tradingview.com/?utm_medium=lwc-link&utm_campaign=lwc-chart&utm_source=omnibot-v3" target="_blank" rel="noreferrer">Charting by TradingView</a>
        </div>
      </div>
    </div>
  );
}