"use client";
import * as React from "react";
import { cn } from "@/lib/utils";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, ...props }, ref) => (
    <input ref={ref} data-slot="input" className={cn("uiInput", className)} {...props} />
  ),
);
Input.displayName = "Input";

export { Input };
