import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("uiBadge", {
  variants: {
    variant: {
      default: "uiBadgeDefault",
      success: "uiBadgeSuccess",
      warning: "uiBadgeWarning",
      destructive: "uiBadgeDestructive",
      outline: "uiBadgeOutline",
      secondary: "uiBadgeSecondary",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
