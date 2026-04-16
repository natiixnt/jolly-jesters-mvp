/**
 * Robust fallback system - barrel export.
 *
 * Usage:
 *   import { executeWithFallback, isFallbackAvailable, ... } from '@/robust';
 */

// Main chain orchestrator
export {
    initFallbackChain,
    destroyFallbackChain,
    executeWithFallback,
    isFallbackAvailable,
    getFallbackChainStatus,
    type FallbackContext,
} from './fallbackChain';

// Error classification
export { classifyError, isSevereBlock, isResultBlocked } from './errorClassifier';

// Cost calculation
export { calculateTaskCost, attachCost, type CostInput } from './costCalculator';

// Concurrency
export { getConcurrencyStats, withSlot } from './concurrencyLimiter';

// Sticky proxy
export { getStickyProxy, getActiveSessionCount, invalidateStickySession } from './stickyProxy';

// Config
export { robustConfig } from './config';

// Types
export type {
    StrategyName,
    FallbackLevel,
    ProxyType,
    AntidetectTool,
    CostBreakdown,
    RobustMetadata,
    RobustFetchResult,
    FetchStrategy,
} from './types';
export { defaultMetadata } from './types';
