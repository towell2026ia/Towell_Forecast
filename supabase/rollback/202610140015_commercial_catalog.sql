-- Separate authorization required. Roll back only an EMPTY, unused schema.
begin;
lock table public.product_commercial_classifications,public.commercial_models,
  public.commercial_colors,public.commercial_sizes in access exclusive mode;
do $$ begin
  if exists(select 1 from public.product_commercial_classifications)
      or exists(select 1 from public.commercial_models) or exists(select 1 from public.commercial_colors)
      or exists(select 1 from public.commercial_sizes) then
    raise exception 'STOP: commercial evidence exists; preserve it and use a reviewed forward rollback';
  end if;
end $$;
drop view public.product_commercial_classifications_current;
drop policy commercial_models_read on public.commercial_models;
drop policy commercial_colors_read on public.commercial_colors;
drop policy commercial_sizes_read on public.commercial_sizes;
drop table public.product_commercial_classifications;
drop table public.commercial_sizes;
drop table public.commercial_colors;
drop table public.commercial_models;
drop function private.commercial_classification_guard();
drop function private.commercial_append_only();
commit;
