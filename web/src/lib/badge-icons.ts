import {
  Award,
  Clapperboard,
  Flame,
  Moon,
  PenLine,
  Send,
  Shapes,
  Sunrise,
  Target,
  Trophy,
  Zap,
} from "lucide-react";

/** The icon each badge is drawn with (Award for any other). */
export const ICONS: Record<string, typeof Award> = {
  first_reel: Clapperboard,
  reels_10: Clapperboard,
  reels_100: Clapperboard,
  reels_500: Clapperboard,
  reels_1000: Trophy,
  day_10: Zap,
  day_25: Zap,
  streak_3: Flame,
  streak_7: Flame,
  streak_30: Flame,
  night_owl: Moon,
  early_bird: Sunrise,
  captions_25: PenLine,
  posts_10: Send,
  variety_5: Shapes,
  quests_7: Target,
};

export { Award };
