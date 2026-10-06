import type { Product } from "./types";

/** Список, разложенный на то, что владелец хочет видеть, и то, что убрал с
 *  глаз, плюс счётчики шапок. Считается в одном месте, потому что числа
 *  связаны: «биддер ведёт N из M» обязано говорить про ВИДИМЫЕ, иначе уборка
 *  в списке не меняет знаменатель и теряет смысл. */
export type VisibilitySplit = {
  visible: Product[];
  hidden: Product[];
  /** Сколько видимых товаров ведёт биддер — числитель «N из M». */
  enabledVisible: number;
  /** Сколько СКРЫТЫХ он всё ещё ведёт. Ноль — тишина; больше нуля — надпись
   *  в шапке раздела: спрятанный товар продолжает тратить деньги, и узнать
   *  об этом владелец должен не раскрывая раздел. */
  enabledHidden: number;
  /** Кампании видимых товаров, без повторов — для подзаголовка страницы. */
  campaignCount: number;
};

export function splitByVisibility(products: Product[]): VisibilitySplit {
  const visible = products.filter((p) => !p.hidden);
  const hidden = products.filter((p) => p.hidden);
  return {
    visible,
    hidden,
    enabledVisible: visible.filter((p) => p.enabled).length,
    enabledHidden: hidden.filter((p) => p.enabled).length,
    campaignCount: new Set(visible.flatMap((p) => p.campaign_ids)).size,
  };
}
