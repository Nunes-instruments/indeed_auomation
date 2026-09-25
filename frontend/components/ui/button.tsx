"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircle } from "lucide-react";

import { cn } from "@/lib/utils";

const buttonVariants = cva("uiButton", {
  variants: {
    variant: {
      default: "uiButtonDefault",
      outline: "uiButtonOutline",
      secondary: "uiButtonSecondary",
      ghost: "uiButtonGhost",
      destructive: "uiButtonDestructive",
      success: "uiButtonSuccess",
      warning: "uiButtonWarning",
      subtle: "uiButtonSubtle",
      link: "uiButtonLink",
    },
    size: {
      default: "uiButtonDefaultSize",
      sm: "uiButtonSm",
      lg: "uiButtonLg",
      icon: "uiButtonIcon",
      iconSm: "uiButtonIconSm",
    },
  },
  defaultVariants: {
    variant: "default",
    size: "default",
  },
});

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
  loadingText?: string;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant,
      size,
      asChild = false,
      loading = false,
      loadingText,
      disabled,
      children,
      ...props
    },
    ref,
  ) => {
    const Comp = asChild ? Slot : "button";

    return (
      <Comp
        data-slot="button"
        data-loading={loading ? "true" : undefined}
        aria-busy={loading || undefined}
        className={cn(buttonVariants({ variant, size }), className)}
        ref={ref}
        disabled={disabled || loading}
        {...props}
      >
        {loading && !asChild ? <LoaderCircle className="buttonSpinner" /> : null}
        {loading && loadingText ? loadingText : children}
      </Comp>
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
