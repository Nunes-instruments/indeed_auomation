"use client";
import * as React from "react";
import { cn } from "@/lib/utils";

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {}

const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, ...props }, ref) => (
    <select ref={ref} data-slot="select" className={cn("uiSelect", className)} {...props} />
  ),
);
Select.displayName = "Select";

export { Select };
